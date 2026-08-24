"""混合检索器：BM25（关键词）+ 向量（语义）+ RRF 融合 + Cross-Encoder 重排序"""
from rank_bm25 import BM25Okapi

from backend.infrastructure.vector_store.embeddings import embed_text,rerank
from backend.infrastructure.vector_store.qdrant_client import search_similar


class HybridRetriever:
    """混合检索器，每个知识库实例对应一个检索器"""

    def __init__(self, documents: list[dict]):
        """
        documents = [{"text": "段落1", "source": "returns.md"}, ...]
        """
        self.documents = documents
        self._bm25_index = None
        if documents:
            # BM25 分词（简单按字切，中文足够）
            tokenized = [list(doc["text"]) for doc in documents]
            self._bm25_index = BM25Okapi(tokenized)

    def bm25_search(self, query: str, top_k: int = 10) -> list[dict]:
        """关键词匹配检索"""
        if not self._bm25_index:
            return []
        query_tokens = list(query)
        scores = self._bm25_index.get_scores(query_tokens)
        # 按分数从高到低取 top_k
        ranked = sorted(
            enumerate(scores), key=lambda x: x[1], reverse=True
        )[:top_k]
        return [
            {
                "idx": idx,
                "score": float(score),
                "text": self.documents[idx]["text"],
                "source": self.documents[idx].get("source", ""),
            }
            for idx, score in ranked
            if score > 0
        ]

    async def vector_search(self, query: str, top_k: int = 10) -> list[dict]:
        """语义向量检索"""
        vector = await embed_text(query)
        if not vector:
            return []
        return search_similar(vector, top_k=top_k)

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        """混合检索 = BM25 + 向量 → RRF 融合 → Top-K"""
        bm25_results = self.bm25_search(query, top_k=top_k * 2)
        vector_results = await self.vector_search(query, top_k=top_k * 2)

        # RRF（Reciprocal Rank Fusion）融合打分
        fused = _rrf_fuse(bm25_results, vector_results)

        # 按融合分排序
        fused.sort(key=lambda x: x["score"], reverse=True)
        reranked = await rerank(query, fused)
        return reranked[:top_k]


def _rrf_fuse(bm25_results: list[dict], vector_results: list[dict], k: int = 60) -> list[dict]:
    """
    RRF 融合算法：不比较原始分数，只比较排名位置
    公式: RRF(d) = Σ 1/(k + rank_i(d))
    """
    scores: dict[str, dict] = {}  # key = text 前 100 字作为去重标识

    def _key(text: str) -> str:
        return text[:100]

    # BM25 贡献
    for rank, item in enumerate(bm25_results):
        key = _key(item["text"])
        scores[key] = {
            "text": item["text"],
            "source": item.get("source", ""),
            "score": 1.0 / (k + rank + 1),
        }

    # 向量贡献（累加）
    for rank, item in enumerate(vector_results):
        key = _key(item["text"])
        rr = 1.0 / (k + rank + 1)
        if key in scores:
            scores[key]["score"] += rr
        else:
            scores[key] = {
                "text": item["text"],
                "source": item.get("source", ""),
                "score": rr,
            }

    return list(scores.values())
