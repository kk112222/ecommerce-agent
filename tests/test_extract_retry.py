"""记忆提炼的可靠性（离线：内存 SQLite + 假 LLM，不联网）

P2-16：提炼是"回复之后"的后台任务，失败以前只写一行日志 —— 用户感受是"记忆时有时无"。
现在：失败落 memory_extract_tasks 表 + 启动重放 + 计数可观测。
"""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.api.routes import chat
from backend.db.models.base import Base
from backend.db.models.memory_task import MemoryExtractTask
from backend.db.models.user_memory import LongTermMemory


@pytest.fixture
async def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,
                            tables=[LongTermMemory.__table__, MemoryExtractTask.__table__])
    monkeypatch.setattr(chat, "AsyncSessionLocal",
                        async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(chat, "_EXTRACT_STATS",
                        {"ok": 0, "skipped": 0, "failed": 0, "retried": 0})
    yield engine
    await engine.dispose()


async def _tasks():
    async with chat.AsyncSessionLocal() as s:
        return (await s.execute(select(MemoryExtractTask))).scalars().all()


async def _drain():
    """等所有后台任务跑完（_spawn 起的任务）"""
    while chat._BACKGROUND_TASKS:
        await asyncio.gather(*list(chat._BACKGROUND_TASKS), return_exceptions=True)


# ==================== 后台任务引用 ====================

async def test_spawn_holds_reference_until_done(db):
    """create_task 的返回值必须被持有，否则任务可能被 GC 掉（记忆就这么静默丢了）"""
    started = asyncio.Event()

    async def job():
        started.set()
        await asyncio.sleep(0)

    task = chat._spawn(job())
    assert task in chat._BACKGROUND_TASKS
    await started.wait()
    await _drain()
    assert task not in chat._BACKGROUND_TASKS      # 完成后自动摘除，集合不会只增不减


# ==================== 失败落表 ====================

async def test_failed_extract_is_recorded_for_retry(db, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("LLM 抽风了")

    monkeypatch.setattr(chat, "_extract_and_save", boom)
    await chat._extract_in_background(1, "s1", "帮我看看这周男装的转化率", "报告正文")

    rows = await _tasks()
    assert len(rows) == 1
    assert rows[0].status == "pending" and rows[0].attempts == 1
    assert "LLM 抽风了" in rows[0].last_error
    assert chat._EXTRACT_STATS["failed"] == 1


async def test_repeated_failure_gives_up_after_three(db, monkeypatch):
    """同一个任务反复失败（比如 Key 失效）→ 转到 failed，别无限重试"""
    async def boom(*a, **k):
        raise RuntimeError("还是不行")

    monkeypatch.setattr(chat, "_extract_and_save", boom)
    await chat._extract_in_background(1, "s1", "帮我看看这周男装的转化率", "报告")
    task_id = (await _tasks())[0].id
    for _ in range(2):                             # 再重放两次 = 累计 3 次失败
        await chat._extract_in_background(1, "s1", "帮我看看这周男装的转化率", "报告",
                                          task_id=task_id)

    rows = await _tasks()
    assert len(rows) == 1                          # 全程只有一行，不重复插
    assert rows[0].attempts == 3 and rows[0].status == "failed"


# ==================== 启动重放 ====================

async def test_pending_tasks_are_replayed_on_startup(db, monkeypatch):
    """进程重启后，上次没跑完的提炼要补跑，而不是永远丢了"""
    async with chat.AsyncSessionLocal() as s:
        s.add(MemoryExtractTask(user_id=1, session_id="s1", status="pending",
                                goal="帮我看看这周男装的转化率", reply="报告正文"))
        await s.commit()

    done: list[str] = []

    async def ok(user_id, sid, goal, reply):
        done.append(goal)

    monkeypatch.setattr(chat, "_extract_and_save", ok)
    report = await chat.retry_pending_extracts()
    await _drain()

    assert report["retried"] == 1 and done == ["帮我看看这周男装的转化率"]
    assert (await _tasks())[0].status == "done"


async def test_successful_extract_marks_task_done(db, monkeypatch):
    """重放成功的任务要标 done，否则下次启动还会被重放一遍"""
    async with chat.AsyncSessionLocal() as s:
        s.add(MemoryExtractTask(user_id=1, session_id="s1", status="pending",
                                goal="帮我看看这周男装的转化率", reply="报告正文"))
        await s.commit()
    task_id = (await _tasks())[0].id

    async def ok(*a, **k):
        return None

    monkeypatch.setattr(chat, "_extract_and_save", ok)
    await chat._extract_in_background(1, "s1", "帮我看看这周男装的转化率", "报告",
                                      task_id=task_id)

    rows = await _tasks()
    assert len(rows) == 1 and rows[0].status == "done"


async def test_trivial_goals_are_skipped(db, monkeypatch):
    """寒暄类不提炼（省成本），且计入 skipped 而不是悄无声息"""
    called = False

    def _llm():
        nonlocal called
        called = True

    monkeypatch.setattr(chat, "create_llm", _llm)
    await chat._extract_and_save(1, "s1", "谢谢", "不客气")

    assert called is False and chat._EXTRACT_STATS["skipped"] == 1
