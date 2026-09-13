"""RAG 检索工具 —— 从知识库中检索相关文档片段"""
import hashlib
import logging

from backend.core.tool.base import BaseTool, ToolSpec, ToolResult
from backend.infrastructure.vector_store.retriever import HybridRetriever

logger = logging.getLogger(__name__)

# 从 Qdrant 加载的全部文档作为 BM25 语料（进程内缓存）
_docs_cache: list[dict] | None = None
_retriever: HybridRetriever | None = None
_fingerprint: str | None = None         # 上次建索引时的语料指纹


def _fingerprint_of(docs: list[dict]) -> str:
    """语料指纹：对全部 (source, text) 排序后取 md5

    为什么不用"点 id 集合"当指纹：现在 build_kb 用 uuid4 生成 id，每次重建都会变、
    比 id 也能发现；但只要哪天改成由内容决定的确定性 id，同长度重建就会被漏掉。
    直接对内容取指纹，两种策略都覆盖，且只在进程内留一个 32 字符的串。
    """
    raw = "\n".join(sorted(f"{d.get('source', '')}\x00{d.get('text', '')}" for d in docs))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _load_docs() -> tuple[list[dict], str]:
    """从 Qdrant 加载所有文档片段 + 语料指纹（分页，超 1000 片也不会漏）"""
    from backend.infrastructure.vector_store.qdrant_client import get_client, COLLECTION_NAME
    c = get_client()
    docs: list[dict] = []
    offset = None
    while True:
        points, offset = c.scroll(collection_name=COLLECTION_NAME, limit=1000,
                                  offset=offset, with_payload=True)
        for p in points:
            docs.append({"text": p.payload.get("text", ""),
                         "source": p.payload.get("source", "")})
        if offset is None:
            return docs, _fingerprint_of(docs)


def get_retriever(force: bool = False) -> HybridRetriever:
    """取检索器；知识库变了就自动重建（P2-12）

    BM25 的语料是进程内缓存的，以前只在首次调用时加载一次 —— 重建知识库后必须重启
    后端才能生效（演示时最容易翻车的地方）。
    这里每次调用都取一次语料指纹：变了就重建索引。
    本地模式的 scroll 是内存里扫一遍，几十上百个片段的开销可以忽略；
    将来换成远端 Qdrant，改成带 TTL 的校验即可（不必每次查询都拉全量）。
    """
    global _retriever, _docs_cache, _fingerprint
    docs, fingerprint = _load_docs()
    if _retriever is None or force or fingerprint != _fingerprint:
        if _retriever is not None:
            logger.info("检测到知识库变更（%d → %d 个片段），重建 BM25 索引",
                        len(_docs_cache or []), len(docs))
        _docs_cache, _fingerprint = docs, fingerprint
        _retriever = HybridRetriever(docs)
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
