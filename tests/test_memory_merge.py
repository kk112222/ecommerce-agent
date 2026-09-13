"""记忆合并 / 召回的 DB 级测试（离线：内存 SQLite + 假向量库 + 假 LLM）

补的是 P3-17 点名的空白：merge_semantic_memories / recall_user_memories 以前零覆盖，
而 P0-2、P1-4、P1-5 全在这片区域。

只用内存库和假向量，不碰真实 data_v3.db / outputs / qdrant：
- `store.AsyncSessionLocal` 换成一个内存 SQLite 的 sessionmaker
- 向量与 qdrant 的四个入口（embed / upsert / delete / search）全换成可控的假实现
"""
import pytest
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.core.memory import store
from backend.db.models.base import Base
from backend.db.models.user_memory import LongTermMemory
from backend.db.models.user_profile import UserProfile
from tests.conftest import FakeLLM


@pytest.fixture
async def memdb(monkeypatch):
    """内存 SQLite + 假向量库；返回 (sessionmaker, 假 qdrant 记录器)"""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,                      # 内存库必须共用同一条连接，否则每次连接都是空库
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,
                            tables=[LongTermMemory.__table__, UserProfile.__table__])
    monkeypatch.setattr(store, "AsyncSessionLocal",
                        async_sessionmaker(engine, expire_on_commit=False))

    # 假 qdrant：points 就是"库里的点 id 集合"，能真实反映删/插的效果
    state = {"points": set(), "upserted": [], "deleted": [], "hits": []}

    def _upsert(docs, collection_name=None):
        state["upserted"].extend(docs)
        state["points"].update(d["id"] for d in docs)

    def _delete(coll, ids):
        state["deleted"].extend(ids)
        state["points"].difference_update(ids)

    monkeypatch.setattr(store, "ensure_collection", lambda *a, **k: None)
    monkeypatch.setattr(store, "embed_batch",
                        lambda texts: _done([[0.1, 0.2] for _ in texts]))
    monkeypatch.setattr(store, "embed_text", lambda text: _done([0.1, 0.2]))
    monkeypatch.setattr(store, "upsert_docs", _upsert)
    monkeypatch.setattr(store, "delete_points", _delete)
    monkeypatch.setattr(store, "list_point_ids", lambda coll=None: set(state["points"]))
    monkeypatch.setattr(store, "search_similar",
                        lambda *a, **k: state["hits"])

    yield state
    await engine.dispose()


async def _done(value):
    """同步值包成 awaitable（假 embed 用）"""
    return value


async def _rows(user_id: int, kind: str | None = None):
    return await store._active_rows(user_id, kind)


def _judge_llm(*verdicts: str) -> FakeLLM:
    """假 LLM：只回答合并判定（JSON 数组）"""
    import json
    return FakeLLM([json.dumps(list(verdicts), ensure_ascii=False)])


# ==================== 合并 ====================

async def test_same_preference_refreshes_without_new_row(memdb):
    """近义刷新（≥0.9）：只加热度，不新增行、不作废"""
    await store.merge_semantic_memories(1, ["负责男装类目的运营"])
    r = await store.merge_semantic_memories(1, ["负责男装类目的运营。"])   # 只差标点
    assert r == {"added": 0, "changed": 0, "kept": 1, "retired": 0,
                 "parallel": 0, "guard": False}
    rows = await _rows(1)
    assert len(rows) == 1 and rows[0].importance == 2      # 刷新过一次热度


async def test_update_verdict_retires_old_row(memdb):
    """LLM 判 update（同一偏好变了）→ 作废旧行、写新行（男装 → 女装）"""
    await store.merge_semantic_memories(1, ["负责男装类目"])
    r = await store.merge_semantic_memories(1, ["负责女装类目"], llm=_judge_llm("update"))
    assert r["changed"] == 1 and r["retired"] == 1
    texts = [x.text for x in await _rows(1)]
    assert texts == ["负责女装类目"]                        # 旧行已失效


async def test_parallel_verdict_keeps_both(memdb):
    """LLM 判 parallel（两条并列偏好）→ 旧行保留、新句也生效（P1-4 的核心修复）"""
    await store.merge_semantic_memories(1, ["关注退货率"])
    r = await store.merge_semantic_memories(1, ["关注退款率"], llm=_judge_llm("parallel"))
    assert r["parallel"] == 1 and r["retired"] == 0
    texts = sorted(x.text for x in await _rows(1))
    assert texts == sorted(["关注退货率", "关注退款率"])    # 两条都在，不再互相顶掉


async def test_judge_failure_falls_back_to_old_behaviour(memdb):
    """判据不可用（没给 llm / 返回乱码）→ 降级回原字面判据，不能崩"""
    await store.merge_semantic_memories(11, ["关注退货率"])
    r = await store.merge_semantic_memories(11, ["关注退款率"])          # llm=None
    assert r["changed"] == 1

    await store.merge_semantic_memories(12, ["关注退货率"])
    r2 = await store.merge_semantic_memories(12, ["关注退款率"],
                                             llm=FakeLLM(["这不是 JSON"]))
    assert r2["changed"] == 1                              # 乱码也降级，不抛异常


async def test_retire_guard_blocks_mass_deletion(memdb):
    """P1-4 护栏：3 条旧记忆本轮只认领 1 条 → 其余不许静默作废"""
    await store.merge_semantic_memories(1, ["负责男装类目", "关注退货率", "每周看一次日报"])
    r = await store.merge_semantic_memories(1, ["负责男装类目"])         # LLM 输出被截断的模拟
    assert r["guard"] is True and r["retired"] == 0
    assert len(await _rows(1)) == 3                        # 一条都没丢


async def test_guard_not_triggered_when_rows_are_claimed(memdb):
    """真的整批换了偏好（每句都被认领）时，护栏不能误拦"""
    await store.merge_semantic_memories(1, ["负责男装类目", "关注退货率", "每周看一次日报"])
    r = await store.merge_semantic_memories(
        1, ["负责女装类目", "关注退款率", "每天看一次日报"], llm=_judge_llm("update"))
    assert r["guard"] is False and r["changed"] == 3 and r["retired"] == 3


async def test_guard_not_applied_to_tiny_profiles(memdb):
    """只有 1~2 条时比例没意义，不套护栏（否则旧偏好永远删不掉）"""
    await store.merge_semantic_memories(1, ["负责男装类目"])
    r = await store.merge_semantic_memories(1, ["完全无关的新偏好：爱喝美式咖啡"])
    assert r["guard"] is False and r["retired"] == 1


async def test_retired_row_keeps_audit_trail_and_drops_vector(memdb):
    """作废 = active=False（行留着可审计）+ 删掉它的向量点"""
    await store.merge_semantic_memories(1, ["负责男装类目"])
    old_pid = (await _rows(1))[0].qdrant_point_id
    await store.merge_semantic_memories(1, ["负责女装类目"], llm=_judge_llm("update"))

    async with store.AsyncSessionLocal() as db:
        all_rows = (await db.execute(
            __import__("sqlalchemy").select(LongTermMemory))).scalars().all()
    assert len(all_rows) == 2                              # 行还在（留痕）
    assert [r.active for r in all_rows].count(False) == 1
    assert old_pid in memdb["deleted"]                     # 向量已清


# ==================== 情景记忆 ====================

async def test_episodic_dedupes_near_identical(memdb):
    """情景记忆 append + 去重：同一件事重复提不重复落"""
    assert await store.save_episodic_memories(1, ["上周做了双十一预热"]) == 1
    assert await store.save_episodic_memories(1, ["上周做了双十一预热。"]) == 0
    assert len(await _rows(1, store.KIND_EPISODIC)) == 1


# ==================== 对账（P1-5） ====================

async def test_reconcile_removes_orphan_vectors(memdb):
    """孤儿向量（qdrant 里有、SQLite 没引用）→ 清掉"""
    await store.merge_semantic_memories(1, ["负责男装类目"])
    memdb["points"].add("orphan-1")                    # 模拟"向量插上了但 DB 提交失败"
    report = await store.reconcile_memory()
    assert report["orphans"] == 1
    assert "orphan-1" not in memdb["points"]
    assert len(await _rows(1)) == 1                    # 事实源那条没受影响


async def test_reconcile_repairs_row_missing_vector(memdb):
    """行在、向量丢了（最坑的"静默降级"）→ 重新向量化补回，功能恢复"""
    await store.merge_semantic_memories(1, ["负责男装类目"])
    pid = (await _rows(1))[0].qdrant_point_id
    memdb["points"].discard(pid)                       # 模拟"向量删了但 DB 没提交"
    report = await store.reconcile_memory()
    assert report["repaired"] == 1 and report["failed"] == 0
    assert pid in memdb["points"]                      # 用同一个 pid 补回，行仍然对得上


async def test_reconcile_fills_row_without_pid(memdb):
    """写时 embedding 失败留下的 pid=None 行 → 对账补向量并把 pid 回填进 DB"""
    await store.merge_semantic_memories(1, ["负责男装类目"])
    async with store.AsyncSessionLocal() as db:
        row = (await db.execute(select(LongTermMemory))).scalars().one()
        row.qdrant_point_id = None
        await db.commit()
    memdb["points"].clear()

    report = await store.reconcile_memory()
    assert report["repaired"] == 1
    assert (await _rows(1))[0].qdrant_point_id in memdb["points"]


async def test_reconcile_dry_run_changes_nothing(memdb):
    """dry-run 只报告不动手（删向量是不可逆的，得让人先看一眼）"""
    await store.merge_semantic_memories(1, ["负责男装类目"])
    memdb["points"].add("orphan-1")
    report = await store.reconcile_memory(dry_run=True)
    assert report["orphans"] == 1 and "orphan-1" in memdb["points"]


async def test_reconcile_is_noop_when_consistent(memdb):
    await store.merge_semantic_memories(1, ["负责男装类目"])
    report = await store.reconcile_memory()
    assert report == {"rows": 1, "repaired": 0, "orphans": 0, "failed": 0, "dry_run": False}


# ==================== 召回 ====================

async def test_recall_falls_back_to_profile_when_empty(memdb):
    """一条记忆都没有 → 退一句话画像兜底，不能返回 None"""
    out = await store.recall_user_memories(1, "今天卖得怎么样")
    assert isinstance(out, str)


async def test_recall_does_not_inflate_importance(memdb):
    """P2-8：召回只刷新 last_access，不能把 importance 顶到饱和"""
    await store.merge_semantic_memories(1, ["负责男装类目"])
    row = (await _rows(1))[0]
    memdb["hits"] = [{"id": row.qdrant_point_id, "score": 0.9, "text": row.text}]

    for _ in range(5):                                  # 连召回 5 次
        await store.recall_user_memories(1, "男装卖得怎么样")

    after = (await _rows(1))[0]
    assert after.importance == 1                        # 没被搜到这件事抬高
    assert after.last_access_at is not None             # 但"最近用过"有记录


async def test_recall_fallback_excludes_episodic(memdb):
    """P2-9：向量没命中时的兜底只补语义记忆，不把情景记忆塞进 prompt"""
    await store.merge_semantic_memories(1, ["关注转化率", "负责男装类目"])
    await store.save_episodic_memories(1, ["上周做了双十一预热"])
    memdb["hits"] = []                                  # 模拟"向量没命中够"

    out = await store.recall_user_memories(1, "随便问问", top_k=3)
    assert "双十一" not in out                           # 情景记忆没被兜底硬塞
    assert "关注转化率" in out and "负责男装类目" in out


async def test_episodic_gets_ttl_and_expires(memdb):
    """P2-10：情景记忆写入带 TTL，过期后不再参与召回（行仍留在库里可审计）"""
    await store.save_episodic_memories(1, ["上周做了双十一预热"], ttl_days=30)
    row = (await _rows(1, store.KIND_EPISODIC))[0]
    assert row.expires_at is not None
    assert (row.expires_at - row.created_at).days == 30

    # 手动把它改成"已过期"，模拟 30 天后
    async with store.AsyncSessionLocal() as db:
        r = (await db.execute(select(LongTermMemory))).scalars().one()
        r.expires_at = store._utcnow() - timedelta(days=1)
        await db.commit()
    assert await _rows(1, store.KIND_EPISODIC) == []    # 读侧自动过滤
