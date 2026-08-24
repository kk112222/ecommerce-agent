"""阶段2 向量记忆 · 数据层验收（阶段B：需先停后端）

qdrant 本地模式单进程独占锁，后端活着时不能查，所以：
  1. 先停掉 uvicorn 后端
  2. 再跑本脚本，直接查 qdrant user_memories + sqlite user_profile 拿落库铁证

用法：cd ecommerce-agent && PYTHONPATH=. .venv/Scripts/python scripts/verify_stage2_db.py
"""
import sys
import sqlite3
import asyncio

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from qdrant_client.models import Filter, FieldCondition, MatchValue

from backend.infrastructure.vector_store.qdrant_client import get_client
from backend.infrastructure.vector_store.embeddings import embed_text


async def main():
    c = get_client()

    # ① user_memories 里 user_id=1 的全部记忆
    pts, _ = c.scroll(collection_name="user_memories", limit=100)  # 新版返回 (points, next_offset) 元组
    mine = [p for p in pts if p.payload.get("user_id") == 1]
    print(f"[1] qdrant user_memories 中 user_id=1 的记忆条数：{len(mine)}")
    for p in mine:
        print(f"     - {p.payload.get('text')}")

    # ② 语义召回：用「标题怎么写」query 看能否召回到刚才的记忆
    qvec = await embed_text("标题怎么写才能提高点击率")
    recall = c.query_points(
        collection_name="user_memories", query=qvec, limit=3, with_payload=True,
        query_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=1))]),
    ).points
    print("[2] 「标题怎么写」语义召回 top3（应命中记忆里标题/点击相关）：")
    for r in recall:
        print(f"     - {r.score:.3f}  {r.payload.get('text')}")

    # ③ 兜底表 user_profile 是否同步更新
    conn = sqlite3.connect("data_v3.db")
    row = conn.execute("SELECT preferences FROM user_profile WHERE user_id=1").fetchone()
    print("[3] user_profile 兜底画像：", (row[0] if row else None))


if __name__ == "__main__":
    asyncio.run(main())
