"""旧长期记忆回填 —— 一次性迁移脚本（2026-09-08 记忆增量式改造后）

背景：改造前长期记忆只存在 qdrant `user_memories` 集合（payload 带 text/user_id/created_at，
无 kind、无 SQLite 行）。改造后「SQLite 行是唯一事实源，qdrant 只是向量索引」，旧点没有对应行
会被召回忽略 → 等于用户记忆静默重置。
本脚本把旧 qdrant 点**补成** long_term_memories 行（kind=semantic，点 id 记在行上），
只插不删、不动原向量 → 零风险，可重复跑（按 qdrant_point_id / 同用户同文本 幂等去重）。

跑法（qdrant 本地模式单进程锁，须先停后端）：
    PYTHONPATH=. .venv/Scripts/python scripts/backfill_legacy_memory.py
"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.disable(logging.CRITICAL)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select

from backend.core.memory.store import MEMORY_COLLECTION, KIND_SEMANTIC
from backend.db.models.user_memory import LongTermMemory
from backend.db.session import AsyncSessionLocal
from backend.infrastructure.vector_store.qdrant_client import get_client


def _parse_ts(s: str | None) -> datetime | None:
    """旧 payload 里 created_at 是 '2026-09-04T21:55:42' 这类 ISO 串，解析失败返回 None 走 now()"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


async def main():
    c = get_client()
    if not c.collection_exists(MEMORY_COLLECTION):
        print("user_memories 集合不存在，无需回填")
        return
    pts, _ = c.scroll(MEMORY_COLLECTION, limit=10000, with_payload=True, with_vectors=False)
    legacy = [p for p in pts if p.payload.get("user_id") is not None and p.payload.get("text")]
    if not legacy:
        print("集合里没有可回填的旧点")
        return

    async with AsyncSessionLocal() as db:
        # 幂等参照：已登记的向量点 id + 同用户已存在的生效文本
        rows = (await db.execute(select(LongTermMemory))).scalars().all()
        registered = {r.qdrant_point_id for r in rows if r.qdrant_point_id}
        existing_text = {(r.user_id, r.text) for r in rows if r.active and r.kind == KIND_SEMANTIC}

        added = skipped = 0
        for p in legacy:
            pid = str(p.id)
            uid = int(p.payload["user_id"])
            text = str(p.payload["text"]).strip()
            if not text:
                continue
            if pid in registered or (uid, text) in existing_text:
                skipped += 1
                continue
            ts = _parse_ts(p.payload.get("created_at"))
            row = LongTermMemory(
                user_id=uid, kind=KIND_SEMANTIC, text=text, qdrant_point_id=pid,
            )
            if ts is not None:                     # 保留原生成时间；没有才走 server_default now()
                row.created_at = ts
            db.add(row)
            existing_text.add((uid, text))          # 同批重复文本只落一条
            added += 1
        await db.commit()
        # 只显示统计，不把整表 dump 出来
        by_user = {}
        for p in legacy:
            uid = int(p.payload["user_id"])
            by_user[uid] = by_user.get(uid, 0) + 1
        print(f"旧点共 {len(legacy)} 条（按用户 {by_user}），本次新增 {added} 行，跳过 {skipped}（已存在/重复）")
    c.close()


if __name__ == "__main__":
    asyncio.run(main())
