"""RAG 端到端质量评测 —— 扰动测试 + judge 忠实度（对应外部评审第四节）

补 rag_eval.py（只测"检没检索到"）没覆盖的一层：**检索到的内容，模型答得老不老实**。

1. 扰动测试 —— 回答跟源走、还是模型自己编/背：
   - V1 换值：把某条款数值在"资料"里改掉，问原问题 → 回答应跟新值（跟随源）
   - V2 清空：资料为空，问原问题 → 不应编出旧值（虚构条款预训练背不到，答不出才对）
   若 V1 还答旧值 / V2 还答旧值 → 模型在 bypass（靠常识/记忆，不真看资料）。
2. judge 忠实度 —— 用独立 prompt 当裁判：
   - 真块作答：判回答是否每个具体事实都能在资料找到出处、没添油加醋
   - 无关块作答（真值被扣掉）：好模型应说"资料里没有"，自信编条款 = 幻觉

设计要点：
- 复用 scripts/build_kb.py 的切块 + scripts/rag_eval.py 的 GOLDEN 片段做真值来源
- 只调对话 LLM，不碰 qdrant → 后端不用停
- 真值片段本来就是虚构条款，模型背不到，换值/删值才有判别力
- 每次请求带 90s 超时防挂死；条目并发跑省时间

运行（在 ecommerce-agent 目录）：
  PYTHONPATH=. .venv/Scripts/python scripts/rag_quality_eval.py
"""
import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # 让 backend.* / scripts.* 可 import
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.core.config import settings
from backend.core.llm.base import Message
from backend.core.llm.qwen import QwenLLM

# ---------- 载入既有模块（不改它们，只复用真值/切块） ----------
def _load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

_bk = _load_module("_build_kb", "scripts/build_kb.py")   # parse_file / chunk_text / KB_DIR
GOLDEN = _load_module("_rag_eval", "scripts/rag_eval.py").GOLDEN
parse_file, chunk_text, KB_DIR, SUPPORTED = (
    _bk.parse_file, _bk.chunk_text, _bk.KB_DIR, _bk.SUPPORTED)

llm = QwenLLM(settings.dashscope_api_key, settings.llm_model)
LLM_TIMEOUT = 90  # 单次请求上限，防挂死

# ---------- 扰动条目：(问题, 旧值片段, 新值片段, 新值判别词) ----------
# 旧值片段逐字取自知识库（run 时会自检，找不到就跳过并提示）
PERTURBS = [
    ("秒杀活动一个账号最多能买几件？", "限购 1 件", "限购 5 件", "5 件"),
    ("满多少钱可以包邮？", "满 99 元包邮", "满 129 元包邮", "129"),
    ("消费积分会过期吗，能一直攒着吗？", "积分有效期 24 个月", "积分有效期 6 个月", "6 个月"),
    ("电子发票没收到，一个月能重新发送几次？", "最多重发 3 次", "最多重发 5 次", "5 次"),
    ("运费险能赔多少钱？", "8-12 元", "10-15 元", "15 元"),
    ("注销账号提交后还能反悔吗，限期内？", "15 天冷静期", "30 天冷静期", "30 天"),
]


def load_chunks() -> list[dict]:
    """解析 KB 源文件 → 切块（和 build_kb 同一套），不碰 qdrant"""
    chunks = []
    for fp in sorted((p for pat in SUPPORTED for p in KB_DIR.glob(pat)), key=lambda p: p.name):
        text = parse_file(fp)
        for c in chunk_text(text, fp.name):
            chunks.append({"text": c["text"], "source": fp.name})
    return chunks


def find_chunk(chunks, phrase: str):
    """返回含 phrase 的第一个块；找不到返回 None"""
    for c in chunks:
        if phrase in c["text"]:
            return c
    return None


def norm(s: str) -> str:
    """去空格/小写，让 '5 件' == '5件' 这类误判消失"""
    return re.sub(r"\s+", "", s).lower()


# ---------- 单次问答（带超时） ----------
SYS = ("你是电商平台售后客服。回答只能依据【检索资料】里写的内容，并标出每条依据的来源文件名。"
       "资料里没有的信息，直接说“知识库暂无此信息”，绝不编造具体条款、数值或日期。")

async def ask(question: str, context: str | None, temperature: float = 0.2) -> str:
    ctx = context if context else "（知识库未检索到与该问题相关的内容）"
    msgs = [
        Message(role="system", content=SYS),
        Message(role="user", content=f"[检索资料]\n{ctx}\n\n[用户问题]\n{question}\n请用一句话回答。"),
    ]
    resp = await asyncio.wait_for(llm.chat(msgs, temperature=temperature), timeout=LLM_TIMEOUT)
    return resp.content.strip()

JUDGE_SYS = ("你是 RAG 输出质检员。【AI回答】只能依据【检索资料】作答。请判断："
             "faithful —— 回答里每个具体数值/条款/日期是否都能在资料里找到出处"
             "（张冠李戴、无出处就算 false）；"
             "hallucinate —— 回答是否编造了资料里不存在的具体条款/数值/日期。"
             "只输出一行 JSON，不要任何其他文字："
             '{"faithful": true/false, "hallucinate": true/false, "reason": "一句话理由"}')

async def judge(question: str, context: str, answer: str) -> dict:
    msgs = [
        Message(role="system", content=JUDGE_SYS),
        Message(role="user", content=f"[检索资料]\n{context}\n\n[用户问题]\n{question}"
                                     f"\n[AI回答]\n{answer}"),
    ]
    resp = await asyncio.wait_for(llm.chat(msgs, temperature=0.0), timeout=LLM_TIMEOUT)
    text = resp.content.strip()
    m = re.search(r"\{.*\}", text, re.S)  # 取第一段完整 JSON
    if not m:
        return {"faithful": None, "hallucinate": None, "raw": text[:60]}
    try:
        d = json.loads(m.group())
        return {"faithful": d.get("faithful"), "hallucinate": d.get("hallucinate", None)}
    except Exception:
        return {"faithful": None, "hallucinate": None, "raw": text[:60]}


def decoy_chunk(chunks, true_chunk, phrase: str):
    """找一块无关资料：优先 account.txt，其次任意不同源、且不含答案片段的块"""
    prefer = ["account.txt", "faq.md", "returns.md", "shipping.md", "invoice.html",
              "membership.md", "promo.docx", "cross_border.pdf"]
    for src in prefer:
        if src == true_chunk["source"]:
            continue
        cand = next((c for c in chunks if c["source"] == src), None)
        if cand and phrase not in cand["text"]:
            return cand
    return None


# ==================== 1. 扰动测试 ====================
async def run_perturb(chunks):
    print("=" * 60)
    print("扰动测试（回答跟源走？会不会 bypass / 编造？）")
    sem = asyncio.Semaphore(4)

    async def one(q, old, new, key):
        real = find_chunk(chunks, old)
        if real is None:
            print(f"  [跳过] 文档里找不到片段 {old!r}（KB 文本变了？）", flush=True)
            return None
        swapped = real["text"].replace(old, new)
        async with sem:
            a1 = await ask(q, swapped)     # V1：资料给了新值
            a2 = await ask(q, None)        # V2：资料为空
        ok1 = norm(key) in norm(a1) and norm(old) not in norm(a1)
        ok2 = norm(old) not in norm(a2)
        print(f"  [{real['source']}] V1跟随={ok1} V2不编={ok2}", flush=True)
        return {"src": real["source"], "q": q, "old": old, "new": new,
                "ok1": ok1, "ok2": ok2, "a1": a1, "a2": a2}

    recs = [r for r in await asyncio.gather(*(one(*it) for it in PERTURBS)) if r]
    tot = len(recs)
    ok1 = sum(r["ok1"] for r in recs); ok2 = sum(r["ok2"] for r in recs)
    print(f"\nV1 换值跟随源     {ok1}/{tot} —— 给了新值回答跟新值（不 bypass）")
    print(f"V2 空资料不编造   {ok2}/{tot} —— 没资料时答不出旧条款（不幻觉）")
    for r in recs:
        if not r["ok1"]:
            print(f"  [V1失败] {r['src']} {r['q']}\n    期望跟 {r['new']!r}，实际：{r['a1'][:80]}")
        if not r["ok2"]:
            print(f"  [V2失败] {r['src']} {r['q']}\n    期望不提 {r['old']!r}，实际：{r['a2'][:80]}")
    print("\n怎么读：V1 越低越像模型靠常识/记忆答题（真 bypass）；V2 越低越像没资料也敢编。")
    return recs


# ==================== 2. judge 忠实度 ====================
async def run_faithfulness(chunks):
    print("\n" + "=" * 60)
    print("judge 忠实度（回答是否有出处 / 扣掉真块会不会幻觉）")
    sem = asyncio.Semaphore(4)
    stats = {"pf": 0, "pft": 0, "nh": 0, "nht": 0}
    samples = []

    async def one(q, old):
        true = find_chunk(chunks, old)
        if true is None:
            return None
        decoy = decoy_chunk(chunks, true, old)
        if decoy is None:
            return None
        async with sem:
            a_pos = await ask(q, true["text"])     # 正例：给真块
            v_pos = await judge(q, true["text"], a_pos)
            a_neg = await ask(q, decoy["text"])    # 反例：只给无关块
            v_neg = await judge(q, decoy["text"], a_neg)
        pos_faith = v_pos.get("faithful") is True
        neg_clean = (v_neg.get("hallucinate") is not True) and norm(old) not in norm(a_neg)
        stats["pft"] += 1; stats["nht"] += 1
        if pos_faith: stats["pf"] += 1
        if neg_clean: stats["nh"] += 1
        print(f"  [{true['source']}] 忠实={pos_faith} 无幻觉={neg_clean}", flush=True)
        samples.append((true["source"], q, a_pos, a_neg))
        return None

    await asyncio.gather(*(one(q, old) for q, old, _, _ in PERTURBS))
    print(f"\n正例（真块作答）忠实率：{stats['pf']}/{stats['pft']} —— 有出处、不添油加醋")
    print(f"反例（只给无关块）无幻觉：{stats['nh']}/{stats['nht']} —— 没编出被扣掉的条款")
    print("\n样例（抽前 2 条人工复核）：")
    for src, q, ap, an in samples[:2]:
        print(f"  [{src}] {q}\n    正例答：{ap[:60]}\n    反例答：{an[:60]}")
    print("\n怎么读：正例忠实率低 → 回答添油加醋/张冠李戴；反例无幻觉低 → 系统提示没约束住。")
    return stats


async def main():
    chunks = load_chunks()
    print(f"离线切块 {len(chunks)} 个（来源：{sorted({c['source'] for c in chunks})}）")
    await run_perturb(chunks)
    await run_faithfulness(chunks)


if __name__ == "__main__":
    asyncio.run(main())
