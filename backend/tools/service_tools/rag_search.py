"""RAG 检索工具 —— 从知识库中检索相关文档片段"""
from pathlib import Path

from backend.core.tool.base import BaseTool, ToolSpec, ToolResult
from backend.infrastructure.vector_store.retriever import HybridRetriever

# 构建时从 Qdrant 加载全部文档作为 BM25 语料
_docs_cache: list[dict] | None = None
_retriever: HybridRetriever | None = None


def _load_docs() -> list[dict]:
    """从 Qdrant 加载所有文档片段"""
    from backend.infrastructure.vector_store.qdrant_client import get_client, COLLECTION_NAME
    c = get_client()
    points, _ = c.scroll(collection_name=COLLECTION_NAME, limit=1000, with_payload=True)
    return [
        {"text": p.payload.get("text", ""), "source": p.payload.get("source", "")}
        for p in points
    ]


def get_retriever() -> HybridRetriever:
    global _retriever, _docs_cache
    if _retriever is None:
        _docs_cache = _load_docs()
        _retriever = HybridRetriever(_docs_cache)
    return _retriever


class RAGTool(BaseTool):
    """从知识库检索相关文档片段"""

    spec = ToolSpec(
        name="search_knowledge_base",
        description="从电商知识库中检索相关文档（退货政策、物流说明、常见问题等）。用于回答用户关于政策、流程、售后等问题。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要检索的问题或关键词"},
            },
            "required": ["query"],
        },
    )

    async def execute(self, query: str) -> ToolResult:
        retriever = get_retriever()
        results = await retriever.search(query, top_k=5)

        if not results:
            return ToolResult(success=True, data={"结果": "未找到相关文档"})

        formatted = [
            f"[{r['source']}] {r['text'][:300]}"
            for r in results
        ]
        return ToolResult(
            success=True,
            data={"检索结果": formatted},
        )
