"""知识库缓存失效（离线：假 _load_docs，不碰 qdrant）

P2-12：BM25 语料以前只在首次调用时加载一次，重建知识库后必须重启后端才生效
（演示时最容易翻车）。现在按"片段 id 指纹"判断是否重建。
"""
import pytest

from backend.tools.service_tools import rag_search


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """每个用例从干净的缓存开始，别让上一个用例的检索器串进来"""
    monkeypatch.setattr(rag_search, "_retriever", None)
    monkeypatch.setattr(rag_search, "_docs_cache", None)
    monkeypatch.setattr(rag_search, "_fingerprint", None)


def _fake_kb(monkeypatch, docs: list[tuple[str, str]]):
    """把 _load_docs 换成返回指定语料（text, source），指纹用被测代码自己的算法算"""
    def _load():
        loaded = [{"text": t, "source": s} for t, s in docs]
        return loaded, rag_search._fingerprint_of(loaded)
    monkeypatch.setattr(rag_search, "_load_docs", _load)


def test_unchanged_kb_reuses_retriever(monkeypatch):
    _fake_kb(monkeypatch, [("退货政策", "returns.md")])
    first = rag_search.get_retriever()
    second = rag_search.get_retriever()
    assert first is second                      # 没变就不重建（省下 BM25 建索引的开销）


def test_rebuilt_kb_invalidates_cache(monkeypatch):
    """重建知识库（片段变了）→ 同一个进程里立刻用上新语料，不用重启

    注意语料至少 3 条：BM25 的 idf 在 N=2 时恒为 ln(1)=0，两条语料测不出命中。
    """
    _fake_kb(monkeypatch, [("退货政策", "returns.md"),
                           ("物流时效说明", "shipping.md"),
                           ("会员积分规则", "member.md")])
    old = rag_search.get_retriever()
    assert old.bm25_search("退货", top_k=1)

    _fake_kb(monkeypatch, [("新品预售规则", "presale.md"),
                           ("赠品说明", "gift.md"),
                           ("发票说明", "invoice.md")])
    new = rag_search.get_retriever()

    assert new is not old
    assert new.bm25_search("预售", top_k=1)[0]["source"] == "presale.md"
    assert new.bm25_search("退货", top_k=1) == []      # 旧语料已经不在索引里了


def test_force_reload(monkeypatch):
    _fake_kb(monkeypatch, [("退货政策", "returns.md")])
    first = rag_search.get_retriever()
    assert rag_search.get_retriever(force=True) is not first
