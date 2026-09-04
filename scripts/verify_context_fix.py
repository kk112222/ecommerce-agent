"""轻量加固验证：上下文/上传注入截断 + 记忆时间戳（真实 DB 测试行，跑完自清）

只验证 store.py 三个函数的行为，embedding/qdrant 全 monkeypatch，不真调 API、不碰 qdrant 锁。
运行：PYTHONPATH=. .venv/Scripts/python scripts/verify_context_fix.py
"""
import sys, asyncio
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 先关 SQLAlchemy echo 日志（echo 挂在 sqlalchemy.engine.Engine 子 logger 上，disabled 不向下继承）
import logging
for _name in ("sqlalchemy", "sqlalchemy.engine", "sqlalchemy.engine.Engine"):
    logging.getLogger(_name).disabled = True

from backend.core.memory import store
from backend.db.session import AsyncSessionLocal

TUID, TSID = 987654, "t-verify-ctx"   # 临时测试用户/会话


async def cleanup():
    """物理删除测试行"""
    async with AsyncSessionLocal() as db:
        await db.execute(store.delete(store.UploadedDoc).where(store.UploadedDoc.user_id == TUID))
        await db.execute(store.delete(store.ChatMessage).where(store.ChatMessage.user_id == TUID))
        await db.execute(store.delete(store.ChatSession).where(store.ChatSession.user_id == TUID))
        await db.commit()


async def main():
    await cleanup()

    # ===== 测试1：get_messages 上下文预算（3 条长消息共 ~9000 字，应丢最旧、留最近） =====
    msgs = [("MARK_OLD" + "a" * 2995, "user"), ("MARK_MID" + "b" * 2995, "user"),
            ("MARK_NEW" + "c" * 2995, "user")]
    for content, role in msgs:
        await store.save_message(TSID, TUID, role, content)
    hist = await store.get_messages(TSID, TUID)
    assert len(hist) <= store.MAX_HISTORY_CHARS, f"超预算 {len(hist)}"
    assert "MARK_NEW" in hist, "应保留最新消息"
    assert "MARK_OLD" not in hist, "应丢掉最旧消息（整条）"
    assert len(hist) >= 6000, f"丢过头了 {len(hist)}"
    print(f"PASS 1 历史注入预算：{len(hist)}/{store.MAX_HISTORY_CHARS} 字，丢最旧 MARK_OLD、保留最新 MARK_NEW")

    # ===== 测试2：get_uploaded_docs 超长文件截断（7000 字 → 6000 + 标记） =====
    await store.save_uploaded_doc(TSID, TUID, "big.txt", "B" * 7000)
    docs = await store.get_uploaded_docs(TSID, TUID)
    assert "超长截断" in docs, "缺少截断标记"
    assert "原文共 7000 字" in docs, "标记缺原文长度"
    assert f"】\n{'B' * store.MAX_DOC_CHARS}\n…[原文共 7000 字" in docs, f"截断长度错"
    print(f"PASS 2 上传注入截断：7000 字 → 前 {store.MAX_DOC_CHARS} 字 + 省略标记")

    # ===== 测试3：save_user_memories payload 带 created_at（monkeypatch 不碰 qdrant/API） =====
    captured = {}

    async def _embed_batch(texts):
        return [[0.1] * 4 for _ in texts]

    def _upsert(points, collection_name=None):
        captured["points"] = points

    store.embed_batch, store.upsert_docs = _embed_batch, _upsert
    store.ensure_collection = lambda *a, **k: None
    store.delete_by_user = lambda *a, **k: None

    await store.save_user_memories(TUID, ["我是运营，关注转化率", "负责男装类目"])
    pts = captured.get("points") or []
    assert len(pts) == 2, f"应写入 2 条 {len(pts)}"
    for p in pts:
        assert p["payload"].get("created_at"), f"缺 created_at {p['payload']}"
        assert p["payload"]["user_id"] == TUID
    print(f"PASS 3 记忆 payload：{len(pts)} 条均带 created_at={pts[0]['payload']['created_at']}")

    await cleanup()
    print("全部通过")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(cleanup())
