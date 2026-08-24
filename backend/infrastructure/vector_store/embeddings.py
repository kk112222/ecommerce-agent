"""向量化服务 —— 文字 → 1536 维向量（用于语义搜索）"""
from typing import Optional

from dashscope import TextEmbedding, TextReRank

from backend.core.config import settings


async def embed_text(text: str) -> list[float]:
    """单条文本向量化"""
    resp = TextEmbedding.call(
        model="text-embedding-v3",
        api_key=settings.dashscope_api_key,
        input=[text],
    )
    if resp.status_code == 200 and resp.output and resp.output.get("embeddings"):
        return resp.output["embeddings"][0]["embedding"]
    return []


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量向量化（更省 API 调用次数）"""
    resp = TextEmbedding.call(
        model="text-embedding-v3",
        api_key=settings.dashscope_api_key,
        input=texts,
    )
    if resp.status_code == 200 and resp.output and resp.output.get("embeddings"):
        return [emb["embedding"] for emb in resp.output["embeddings"]]
    return [[] for _ in texts]
async def rerank(query: str, documents: list[dict], top_n: int = 10) -> list[dict]:
    if not documents:
        return []
    resp = TextReRank.call(
        model="gte-rerank-v2",
        api_key=settings.dashscope_api_key,
        query=query,
        documents=[d["text"] for d in documents],
        top_n=top_n,
    )
    if resp.status_code != 200:
        return documents
    reranked = sorted(resp.output.results, key=lambda x: x.relevance_score, reverse=True)
    result = []
    for r in reranked:
        doc = dict(documents[r.index])
        doc["rerank_score"] = r.relevance_score
        result.append(doc)
    return result