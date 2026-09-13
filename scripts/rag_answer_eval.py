"""RAG 回答层评测（P3-18）：检索对了 ≠ 答对了

`rag_eval.py` 量的是"片段有没有被召回"；这个脚本量的是下游那一半 ——
**答案是不是忠实于召回的片段、有没有幻觉**。三个指标：

  ① 事实命中率：答案有没有给出真值片段表达的那条事实（LLM 判，容忍措辞差异）
  ② 忠实度 1-5：每个事实性陈述是否都能在召回片段里找到依据（LLM 判）
  ③ 数字越界：答案里出现的数字，是否都能在召回片段里逐字找到 —— 幻觉最典型的形态。
     这一项是**确定性**的（正则 + 子串），不看 LLM 脸色；但它只筛不判：
     匹配刻意偏松（子串而非词），所以"10000 写成 1 万"这类不会误报，
     真正会被误报的是答案自己算出来的数（8+18 → 26）—— 输出的是"待人工复核"清单而不是扣分。

外加一个扰动测试：把知识库里的包邮门槛改掉（**不动真实 KB**，写进临时集合），
同一个问题再问一次 —— 答案跟着变，才证明它真在查知识库，而不是背预训练里的常识。
这是回答层独有的证据：检索层评测证明不了这一点。

运行（需先停后端 —— qdrant 本地模式单进程锁）：
  PYTHONPATH=. .venv/Scripts/python scripts/rag_answer_eval.py          # 抽样 15 条
  PYTHONPATH=. .venv/Scripts/python scripts/rag_answer_eval.py --all    # 全量（慢且费 token）

真值来自 rag_eval.GOLDEN，两个脚本共用一份评测集，改了这里那里也一起变。
"""
import asyncio
import re
import sys
import time
import uuid

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.agents.customer_service.service_agent import ServiceAgent
from backend.core.llm.base import Message
from backend.core.llm.factory import create_llm
from backend.core.tool.registry import ToolRegistry
from backend.infrastructure.vector_store.embeddings import embed_batch
from backend.infrastructure.vector_store.qdrant_client import (
    COLLECTION_NAME, ensure_collection, get_client, upsert_docs,
)
from backend.infrastructure.vector_store.retriever import HybridRetriever
from backend.tools import register_all_tools
from backend.tools.service_tools import rag_search
from backend.tools.service_tools.rag_search import _load_docs
from scripts.rag_eval import GOLDEN

# 存档原始注入点：下面会把 rag_search.get_retriever 换成自己的检索器，
# 换完就不能再从模块属性上拿回真身了
_ORIG_GET_RETRIEVER = rag_search.get_retriever

DEFAULT_LIMIT = 15
JUDGE_PROMPT = """你在评审一个 RAG 问答系统的回答质量。给你【用户问题】【检索到的资料片段】【系统回答】【这条问题的真值片段】。

请判断两件事，只输出一个 JSON，不要任何其它文字：
{"fact_hit": true/false, "faithfulness": 1-5, "unsupported_claims": ["..."]}

1. fact_hit：回答是否给出了真值片段所表达的那条事实（措辞不同没关系，意思对上即可）。
   注意：真值片段是知识库里的原文子串，答案只要没答到这个点就算 false。
2. faithfulness：回答里每个事实性陈述是否都能在【检索到的资料片段】里找到依据。
   5 = 全部有据；3 = 大部分有据但有少量自行发挥；1 = 大量内容资料里根本没有。
3. unsupported_claims：只列资料里找不到依据的事实性陈述（没有就空数组）。"""

# 扰动测试：改一个"预训练绝对背不到"的虚构数字（包邮门槛）
PERTURB_OLD, PERTURB_NEW = "满 99 元包邮", "满 199 元包邮"
PERTURB_Q = "满多少钱可以包邮？"
PERTURB_COLLECTION = "kb_perturb_test"   # 临时集合，测完即删，真实 kb_docs 不动

# 从回答里抽"满 N 元…包邮"的门槛数字 —— 直接对准被扰动的那个语义点。
# 为什么不做字面匹配：答案会加 markdown 加粗、会改写成"满 199 元 即可享受包邮"，
# 字面短语匹配会误判成"没走检索"。抽数字再比对基线，才既稳又对准语义。
# （也不能用"答案里有没有 99"来判断：199 里就含 99，且"不满 99 元"是另一个未扰动的档位，
#   本来就该照实保留 —— 这条是写第一版断言时踩的坑）
_THRESHOLD_RE = re.compile(r"满\s*(\d+)\s*元[^。\n]{0,15}?包邮")


def free_shipping_threshold(answer: str) -> str | None:
    """抽回答里声明的包邮门槛（去掉 markdown 加粗和空格再匹配）"""
    plain = answer.replace("*", "").replace(" ", "")
    m = _THRESHOLD_RE.search(plain)
    return m.group(1) if m else None


class RecordingRetriever:
    """录制代理：记下生产路径真正检索到的片段，用来核对答案有没有超出这些依据

    不侵入生产代码 —— ServiceAgent → RAGTool → get_retriever() 拿到的就是这个代理，
    行为完全一致，只是顺手留了一份"LLM 当时看到了什么"。
    """

    def __init__(self, inner: HybridRetriever):
        self.inner = inner
        self.seen: list[dict] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        results = await self.inner.search(query, top_k=top_k)
        self.seen.extend(results)
        return results


def _numbers(text: str) -> list[str]:
    """抽数字（含小数）。用于"答案里的数字必须能在片段里找到"这条确定性检查"""
    return re.findall(r"\d+(?:\.\d+)?", text)


def numeric_overflows(answer: str, chunks: str) -> list[str]:
    """答案里"片段中根本不存在"的数字（幻觉最典型的形态）

    纯函数、可离线测：取答案的每个数字，逐个在召回片段里找字面出现。
    只筛不判 —— 答案自己算出来的数（8+18 → 26）会被误报，所以输出的是"待人工复核"清单。
    """
    return [n for n in _numbers(answer) if n not in chunks]


def _build_service(retriever):
    """按生产接法组装：真 LLM + 真工具注册中心 + 被录制的检索器"""
    llm = create_llm()
    registry = ToolRegistry()
    register_all_tools(registry, llm)
    rag_search.get_retriever = lambda force=False: retriever   # 只在本脚本内替换注入点
    return ServiceAgent(llm, registry), llm


async def _judge(llm, question: str, chunks: str, answer: str, truth: str) -> dict:
    import json
    prompt = (f"【用户问题】\n{question}\n\n【检索到的资料片段】\n{chunks}\n\n"
              f"【系统回答】\n{answer}\n\n【这条问题的真值片段】\n{truth}")
    resp = await llm.chat([Message(role="system", content=JUDGE_PROMPT),
                           Message(role="user", content=prompt)], temperature=0)
    text = re.sub(r"^```(?:json)?|```", "", resp.content.strip(), flags=re.M)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"fact_hit": False, "faithfulness": 0, "unsupported_claims": ["判据返回非法 JSON"]}


async def eval_answers(cases: list[tuple[str, str, str]]) -> bool:
    """逐条跑生产回答路径，统计三个指标"""
    retriever = RecordingRetriever(rag_search.get_retriever(force=True))
    service, llm = _build_service(retriever)
    print(f"回答层评测：{len(cases)} 条（真值集与 rag_eval 共用）\n")

    hits = faithful_sum = ran = 0
    failures: list[str] = []
    overflows: list[tuple[str, list[str]]] = []
    t0 = time.time()

    for i, (q, truth, src) in enumerate(cases, 1):
        retriever.seen.clear()
        # 单条失败不炸整轮：跑评测最容易遇到的是 Key 失效/欠费/限流，
        # 崩在第一条上只会给一个 traceback，看不出"跑到第几条、为什么停"
        try:
            answer = await service.run(goal=q)
            chunks = "\n".join(r["text"] for r in retriever.seen) or "（没检索到任何片段）"
            verdict = await _judge(llm, q, chunks, answer, truth)
        except Exception as e:
            failures.append(f"{q} → {type(e).__name__}: {str(e)[:140]}")
            print(f"{i:>2}. [{src:<14}] ✗ 跳过（{type(e).__name__}）{q}")
            continue

        ran += 1
        # ③ 数字越界（确定性）：答案里的数字必须能在召回片段里找到
        bad = numeric_overflows(answer, chunks)
        if bad:
            overflows.append((q, bad))
        hits += bool(verdict.get("fact_hit"))
        faithful_sum += int(verdict.get("faithfulness") or 0)
        flag = "✓" if verdict.get("fact_hit") else "✗"
        print(f"{i:>2}. [{src:<14}] {flag} 忠实度 {verdict.get('faithfulness')}/5  {q}")
        if bad:
            print(f"     ⚠ 数字待复核：{bad}")
        if verdict.get("unsupported_claims"):
            print(f"     · 无据陈述：{verdict['unsupported_claims'][:2]}")

    if not ran:
        print(f"\n全部 {len(cases)} 条都没跑成 —— 大概率是 LLM 不可用（Key 失效 / 账户欠费 / 限流）。")
        for f in failures[:3]:
            print(f"  · {f}")
        return False

    print(f"\n{'指标':<16}{'结果':>12}")
    print(f"{'① 事实命中率':<16}{hits / ran:>11.0%}  ({hits}/{ran})")
    print(f"{'② 平均忠实度':<16}{faithful_sum / ran:>10.2f}/5")
    print(f"{'③ 数字越界条数':<16}{len(overflows):>11}  ({len(overflows)}/{ran} 条答案含片段外的数字)")
    print(f"\n耗时 {time.time() - t0:.0f}s。数字越界是筛查不是判罪，出现时人工复核那几条。")
    if failures:
        print(f"另有 {len(failures)} 条因调用失败跳过（指标只按跑成功的 {ran} 条算）")
    return True


async def perturbation_test() -> bool:
    """扰动测试：改掉包邮门槛再问同一题，答案必须跟着变（证明真走了检索）

    做法是**基线对照**：先在真实知识库上问一次拿到基线答案，再在扰动语料上问一次，
    两次抽出的包邮门槛不同 → 说明答案来自检索内容而非模型记忆。
    """
    print(f"\n{'=' * 60}\n扰动测试：把知识库里的「{PERTURB_OLD}」改成「{PERTURB_NEW}」\n")
    docs, _ = _load_docs()
    if not any(PERTURB_OLD in d["text"] for d in docs):
        print(f" 跳过：知识库里找不到 {PERTURB_OLD!r}（KB 可能改过）")
        return False

    # ① 基线：真实知识库上问同一题（此时注入点指向真实 kb_docs）
    baseline_service, _bllm = _build_service(RecordingRetriever(_ORIG_GET_RETRIEVER(force=True)))
    try:
        baseline = free_shipping_threshold(await baseline_service.run(goal=PERTURB_Q))
    except Exception as e:
        print(f" 跳过：基线问答调用失败（{type(e).__name__}: {str(e)[:100]}）")
        return False
    print(f" 基线（真实知识库）声明的包邮门槛：{baseline!r}")
    if baseline is None:
        print(" 跳过：基线回答里没抽到包邮门槛，扰动对比失去参照")
        return False

    perturbed = [{"text": d["text"].replace(PERTURB_OLD, PERTURB_NEW),
                 "source": d["source"]} for d in docs]
    print(f" 扰动语料 {len(perturbed)} 个片段（只改了 {sum(PERTURB_OLD in d['text'] for d in docs)} 处）")

    # ② 重新向量化写进临时集合（真实调用 embedding，不用旧向量糊弄）
    ensure_collection(PERTURB_COLLECTION)
    points = []
    for i in range(0, len(perturbed), 10):
        batch = perturbed[i:i + 10]
        vectors = await embed_batch([c["text"] for c in batch])
        for chunk, vec in zip(batch, vectors):
            if vec:
                points.append({"id": str(uuid.uuid4()), "vector": vec,   # 本地模式要求 UUID
                               "payload": {"text": chunk["text"], "source": chunk["source"]}})
    upsert_docs(points, collection_name=PERTURB_COLLECTION)
    print(f" 已写入临时集合 {PERTURB_COLLECTION}（{len(points)} 点），真实 {COLLECTION_NAME} 未动")

    # ③ 只在本进程内把向量检索指向临时集合（生产代码零改动）
    import backend.infrastructure.vector_store.retriever as R
    original = R.search_similar
    R.search_similar = lambda vec, top_k=10, **kw: original(
        vec, top_k=top_k, collection_name=PERTURB_COLLECTION)

    ok, removed = False, 0
    try:
        retriever = RecordingRetriever(HybridRetriever(perturbed))   # BM25 也用扰动语料
        service, _ = _build_service(retriever)
        answer = await service.run(goal=PERTURB_Q)
        after = free_shipping_threshold(answer)
        # ④ 对比两次门槛：变了才说明答案来自检索内容
        print(f"\n 问：{PERTURB_Q}\n 答：{answer.strip()[:160]}")
        print(f"\n 扰动后声明的包邮门槛：{after!r}")
        ok = after == "199" and after != baseline
        print(" 结论：" + (f"门槛从 {baseline} → {after}，答案跟着知识库变了 → 确实在检索，"
                          "不是背预训练常识 ✅" if ok else "答案没跟着变 → 疑似没走检索 ⚠"))
    finally:
        R.search_similar = original
        c = get_client()   # 清理临时集合的点，别留垃圾
        if c.collection_exists(PERTURB_COLLECTION):
            pts, _ = c.scroll(collection_name=PERTURB_COLLECTION, limit=10000, with_payload=False)
            if pts:
                c.delete(collection_name=PERTURB_COLLECTION, points_selector=[p.id for p in pts])
                removed = len(pts)
        print(f" 已清理临时集合（{removed} 点）")
    return ok


async def main():
    # --limit N 快速抽检（调试脚本本身用），--all 跑全量，默认均匀抽 15 条覆盖各来源
    limit = DEFAULT_LIMIT
    for i, a in enumerate(sys.argv):
        if a == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
    if "--all" in sys.argv:
        cases = GOLDEN
    else:
        step = max(1, len(GOLDEN) // limit)
        cases = GOLDEN[::step]
    if await eval_answers(cases):
        await perturbation_test()
    else:
        print("\n跳过扰动测试（回答层评测一条都没跑成，先解决 LLM 可用性）")
    get_client().close()   # 显式关闭落盘，避免解释器析构时刷 ImportError
    print("\n什么时候重跑：改了 prompt / 换了模型 / 调了 top_k 或 rerank —— 检索数字不变但答案可能变差。")


if __name__ == "__main__":
    asyncio.run(main())
