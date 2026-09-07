"""长期记忆增量改造的端到端验证（外部评审 M4/M5/M6 + M1/M3 收尾）

覆盖点：
1. extractor 双桶解析（stub LLM，不碰网络）
2. merge 增量写路径：add / keep / change(作废重写) / retire(旧条被 LLM 丢弃)
3. episodic append + 去重
4. recall 加权召回（相似 × 重要 × 新鲜度）
5. 管理接口的数据层：list / 逐条 delete（连带删向量）
用临时用户跑，测完清理，不污染真实数据。
"""
import asyncio
import logging
import sys
import time
from pathlib import Path

logging.disable(logging.CRITICAL)                                  # 测试脚本全局静音，只看断言输出

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import backend.core.memory.store as store
from backend.core.memory.extractor import ProfileExtractor
from backend.core.memory.store import (
    merge_semantic_memories, save_episodic_memories, recall_user_memories,
    list_long_term_memories, delete_long_term_memory, semantic_snapshot,
)
from backend.db.models.base import Base
from backend.db.models.user import User
from backend.db.models.user_memory import LongTermMemory
from backend.db.models.user_profile import UserProfile
from backend.db.session import AsyncSessionLocal, engine
from backend.infrastructure.vector_store.qdrant_client import delete_by_user
from sqlalchemy import delete


class _Msg:
    def __init__(self, content: str):
        self.content = content


class _StubLLM:
    """返回固定 JSON 的假 LLM：只测 extractor 解析链路"""
    async def chat(self, messages, temperature=0.0):
        return _Msg('{"semantic": ["负责男装类目"], "episodic": ["用户这周男装转化率掉到 2%"]}')


async def _purge(uid: int):
    """彻底清空该用户的记忆（DB 行 + qdrant 点），保证脚本可重复跑不被上次残留干扰"""
    delete_by_user(store.MEMORY_COLLECTION, uid)
    async with AsyncSessionLocal() as db:
        await db.execute(delete(LongTermMemory).where(LongTermMemory.user_id == uid))
        await db.execute(delete(UserProfile).where(UserProfile.user_id == uid))
        await db.commit()


async def _cleanup(uid: int):
    await _purge(uid)
    async with AsyncSessionLocal() as db:
        await db.execute(delete(User).where(User.id == uid))
        await db.commit()


async def main():
    async with engine.begin() as conn:          # 幂等补建新表 long_term_memories
        await conn.run_sync(Base.metadata.create_all)

    # ---- 临时用户 ----
    uname = f"memtest_{int(time.time())}"
    async with AsyncSessionLocal() as db:
        u = User(username=uname, name="记忆验证临时用户", password_hash="x")
        db.add(u)
        await db.commit()
        uid = u.id
    tag = f"verify-{uid}"
    await _purge(uid)          # 清掉可能复用同 id 的历史残留，保证幂等
    try:
        # 1) extractor 双桶解析
        ex = ProfileExtractor(_StubLLM())
        out = await ex.extract("用户：看看转化率\n助手：已帮你统计", old_profile="① 负责男装类目")
        assert set(out) == {"semantic", "episodic"}, out
        assert "男装" in out["semantic"][0] and "2%" in out["episodic"][0], out
        print("[1] extractor 双桶解析 ok:", len(out["semantic"]), "语义 /", len(out["episodic"]), "情景")

        # 2) merge 增量：先加 2 条
        r = await merge_semantic_memories(uid, ["负责男装类目", "关注转化率"], source_session=tag)
        assert r["added"] == 2, r
        snap = await semantic_snapshot(uid)
        assert "男装" in snap and "转化率" in snap, snap
        print("[2] merge 首次添加 ok:", r)

        # 再喂：两条原文不变(keep) + 一条全新(add)
        r = await merge_semantic_memories(uid, ["负责男装类目", "关注转化率", "想拓展美妆类目"])
        assert r["added"] == 1 and r["kept"] == 2 and r["retired"] == 0, r
        print("[2b] merge 幂等 keep+add ok:", r)

        # 偏好变了：负责男装类目→女装（0.45~0.9 区间）→ 旧行作废重写
        r = await merge_semantic_memories(uid, ["负责女装类目", "关注转化率", "想拓展美妆类目"])
        assert r["changed"] == 1 and r["kept"] == 2 and r["retired"] == 1, r
        snap = await semantic_snapshot(uid)
        assert "女装" in snap and "男装" not in snap, f"变化后画像应替换而非叠加: {snap}"
        print("[2c] merge 变化版作废重写 ok:", r)

        # 3) episodic append + 去重（同句喂两次只落 1 条）
        n1 = await save_episodic_memories(uid, ["用户这周男装转化率掉到 2%"], source_session=tag)
        n2 = await save_episodic_memories(uid, ["用户这周男装转化率掉到 2%"])
        assert n1 == 1 and n2 == 0, (n1, n2)
        print("[3] episodic append+去重 ok:", n1, "/", n2)

        # 4) recall 加权召回（语义 + 情景都该能被"转化率"命中）
        recalled = await recall_user_memories(uid, "现在转化率大概什么水平", top_k=5)
        assert "转化率" in recalled, recalled
        assert "（事件）" in recalled, recalled      # episodic 前缀出现说明跨层召回到
        print("[4] recall 召回 ok:\n   " + recalled.replace("\n", "\n   "))

        # 5) list + 逐条 delete（连带删向量）
        mems = await list_long_term_memories(uid)
        assert len(mems) >= 4, [m["text"] for m in mems]
        kinds = {m["kind"] for m in mems}
        assert kinds == {"semantic", "episodic"}, kinds
        evt = next(m for m in mems if m["kind"] == "episodic")
        assert await delete_long_term_memory(uid, evt["id"]) is True
        after = await list_long_term_memories(uid)
        assert len(after) == len(mems) - 1, (len(after), len(mems))
        print("[5] list/delete ok: 删前", len(mems), "条 → 删后", len(after), "条")

        # 收尾：删光临时记忆（连带向量），避免残留
        for m in await list_long_term_memories(uid):
            await delete_long_term_memory(uid, m["id"])
        print("验证全绿 ✅")
    finally:
        await _cleanup(uid)


if __name__ == "__main__":
    asyncio.run(main())
