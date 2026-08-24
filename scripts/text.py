import asyncio

from dashscope import TextReRank
from backend.core.config import settings

# 模拟从知识库检索回来的文档（假设 RRF 融合后的结果）
candidates = [
    {"text": "本店支持7天无理由退货", "source":
        "returns.md"},
    {"text": "充值后不支持退款", "source": "faq.md"},
    {"text": "苹果15现在有货", "source": "faq.md"},
]


async def main():
    resp = TextReRank.call(
        model="gte-rerank-v2",
        api_key=settings.dashscope_api_key,
        query="怎么退货",
        documents=[d["text"] for d in candidates],
        top_n=3,
    )
    # 用 index 找原文档，按分数从高到低重排
    reranked = sorted(resp.output.results, key=lambda r:
    r.relevance_score, reverse=True)
    for r in reranked:
        doc = candidates[r.index]
        print(f"score={r.relevance_score:.3f}source = {doc['source']}text = {doc['text']}")

if __name__ == "__main__":
    asyncio.run(main())