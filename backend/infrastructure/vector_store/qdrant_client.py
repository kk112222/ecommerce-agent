"""Qdrant 向量库客户端 —— 存储和检索文档片段（支持多 collection，知识库 + 用户记忆共用）"""

from pathlib import Path

from qdrant_client import QdrantClient as QC
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
# 本地文件模式（不上 Docker 时用，开发阶段足够）
_storage_path = Path(__file__).parent.parent.parent.parent / "qdrant_data"
_client: QC | None = None

COLLECTION_NAME = "kb_docs"  # 知识库片段（默认，向后兼容）
VECTOR_SIZE = 1024  # text-embedding-v3 的维度


def get_client() -> QC:
    """获取 Qdrant 客户端（懒加载）"""
    global _client
    if _client is None:
        _storage_path.mkdir(parents=True, exist_ok=True)
        _client = QC(path=str(_storage_path))
    return _client


def ensure_collection(collection_name: str = COLLECTION_NAME):
    """确保集合存在（不存在就创建）"""
    c = get_client()
    if not c.collection_exists(collection_name):
        c.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def upsert_docs(points: list[dict], collection_name: str = COLLECTION_NAME):
    """
    批量写入片段
    points = [
        {"id": "uuid-1", "vector": [0.1, 0.2, ...], "payload": {"text": "...", "source": "returns.md"}},
        ...
    ]
    """
    c = get_client()
    pts = [
        PointStruct(
            id=p["id"],
            vector=p["vector"],
            payload=p.get("payload", {}),
        )
        for p in points
    ]
    c.upsert(collection_name=collection_name, points=pts)


def search_similar(query_vector: list[float], top_k: int = 10,
                   collection_name: str = COLLECTION_NAME, user_id: int | None = None) -> list[dict]:
    """向量相似度检索；user_id 非空时只在该用户的记录里搜（多用户隔离）"""
    c = get_client()
    # 按 user_id 过滤：payload 里的 user_id 字段精确匹配
    query_filter = None
    if user_id is not None:
        query_filter = Filter(must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        ])
    results = c.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
        with_payload=True,
        query_filter=query_filter,
    ).points
    return [
        {
            "id": r.id,
            "score": r.score,
            "text": r.payload.get("text", ""),
            "source": r.payload.get("source", ""),
        }
        for r in results
    ]


def delete_by_user(collection_name: str, user_id: int):
    """删除某个用户的全部记录（重写记忆前先清旧，避免重复积累）"""
    c = get_client()
    c.delete(
        collection_name=collection_name,
        points_selector=Filter(must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        ]),
    )


def delete_points(collection_name: str, point_ids: list[str]) -> None:
    """按点 id 删除一批向量（增量记忆：作废旧版本/用户删单条记忆时用，替代整组清空）"""
    if not point_ids:
        return
    c = get_client()
    c.delete(collection_name=collection_name, points_selector=point_ids)
