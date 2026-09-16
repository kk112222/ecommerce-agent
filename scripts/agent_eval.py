"""多 Agent 评测（编排层）：RAG 那两把尺子量的是"检索/回答"，这一层量的是**编排本身**

为什么需要它（现有评测的盲区）：
- rag_eval.py        量"片段召回没召回"，完全不看 supervisor/planner/executor；
- rag_answer_eval.py 量"单角色回答忠实不忠实"，同样不看编排；
- 而多 Agent 的失败恰恰发生在编排层：**意图分错链路**（该查知识库却去查数据库）、
  **拆解漏维度**（只查了销售就下"营业额为什么低"的结论）、**子任务不调工具靠 LLM 空想**、
  **报告里出现子任务结果里没有的数字**。这些失败在 RAG 指标上完全看不出来。

核心思路：多 Agent 的输出"没有唯一正确答案"，所以拆成两类指标 ——
  确定性（能自动判、不看 LLM 脸色）：
    ① 意图路由准确率    期望链路 vs 实际链路
    ② 工具调用召回/精确  期望工具集合 vs registry 实际被调用的工具
        —— 召回率抓"该查的没查"，精确率抓"多调乱调"
    ③ 必要维度覆盖率    该覆盖的方面有没有出现在证据里（子任务结论 / 最终回答）
    ④ hint 合规率       planner 有没有守"一条子任务一个工具"的契约
    ⑤ 报告数字有据率    报告里的数字能否在子任务结果里逐字找到（复用 rag_answer_eval
        的 numeric_overflows）—— 编排层最典型的幻觉形态
  成本类（顺手带出来，回答"上线怎么控成本"）：
    ⑥ LLM 调用次数 / token / 耗时（每个 case 一个 UsageBudget）

零侵入：生产代码一行不改，三个观察点都是现成的 ——
  - 事件流：build_supervisor(on_event=...) 本来就有 intent/plan/subtask/report；
  - 工具调用：RecordingRegistry 继承 ToolRegistry 只加录制。**关键是重写 subset()** ——
    executor 会给子任务收窄工具集，而父类 ToolRegistry.subset() 返回的是新建的
    ToolRegistry（硬编码类名），不重写就会把录制器丢掉、子任务的调用一条都记不到；
  - 不经 API/DB：直接 graph.invoke(合成 state)，不碰 SQLite、不写长期记忆、不落会话。

判的是"编排行为"，不是"数据对不对"：工具查出来的数字真假由数据层负责，
这里只看有没有去查、查得全不全、报告有没有编。

运行：
  PYTHONPATH=. .venv/Scripts/python scripts/agent_eval.py --dry-run   # 假 LLM 演练评测管线（只省对话 token）
  PYTHONPATH=. .venv/Scripts/python scripts/agent_eval.py --limit 5    # 真 LLM 抽 5 条
  PYTHONPATH=. .venv/Scripts/python scripts/agent_eval.py --all        # 全量 15 条

什么时候重跑：改 planner/executor/intent/supervisor 的 prompt、换模型、加工具、
改 tool_hint 规则 —— 这些改动 RAG 数字不变，但编排质量会变。
"""
import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_OUTPUTS = PROJECT_ROOT / ".agent_eval_outputs"   # 文档落盘沙箱目录，跑完即删

from backend.agents.supervisor import build_supervisor
from backend.core.llm.base import BaseLLM, LLMResponse, ToolCall
from backend.core.llm.budget import BudgetedLLM, UsageBudget
from backend.core.llm.factory import create_llm
from backend.core.tool.registry import ToolRegistry
from backend.infrastructure import doc_output
from backend.tools import register_all_tools
from scripts.rag_answer_eval import numeric_overflows


# ==================== 一、金标集（人工标注的期望行为） ====================
# tools：期望被调用的工具（工具召回率的分母）；dims：本次目标必须覆盖的方面，
# 每维给一组同义关键词，命中任一即算覆盖（避免措辞差异被误判成漏维度）。
# 判据看的是"编排行为"，所以这里的期望要写"必须做对的编排动作"，不写数据细节。

DOC_SAMPLE = ("2026 年第 37 周运营周报。一、本周销售额 8200 元，环比下降 12%。"
              "二、充电宝库存仅剩 8 件，低于安全阈值。三、会员复购率下滑。")

GOLDEN: list[dict] = [
    # ---------- analysis：拆解 + 并行子任务 ----------
    {"goal": "这周营业额为什么低，怎么提高销量", "intent": "analysis",
     "tools": ["query_sales", "check_stock"],
     "dims": [("销售", ["销售", "营业额", "销量"]), ("库存", ["库存", "缺货", "断货"])]},
    {"goal": "查一下充电宝这个商品最近卖得怎么样", "intent": "analysis",
     "tools": ["product_query"],
     "dims": [("商品", ["充电宝"]), ("销量", ["销量", "销售额", "订单"])]},
    {"goal": "哪些商品库存快没了，需要补货", "intent": "analysis",
     "tools": ["check_stock"],
     "dims": [("库存", ["库存", "补货", "阈值"])]},
    {"goal": "各会员等级的消费情况怎么样", "intent": "analysis",
     "tools": ["user_profile"],
     "dims": [("会员", ["会员", "等级", "SVIP", "VIP"])]},
    {"goal": "数码类目这周卖得怎么样", "intent": "analysis",
     "tools": ["category_query"],
     "dims": [("分类", ["数码", "分类", "类目"])]},
    # ---------- content：单角色 + 内容工具 ----------
    {"goal": "给苹果15写三条淘宝标题，关键词 超薄、快充", "intent": "content",
     "tools": ["title_optimizer"],
     "dims": [("标题", ["苹果15", "标题"])]},
    {"goal": "给保温杯写一段小红书推广文案，突出保温12小时", "intent": "content",
     "tools": ["copy_generator"],
     "dims": [("文案", ["保温杯", "文案", "小红书"])]},
    # ---------- service：必须查知识库，不许空想 ----------
    {"goal": "退货要多久？运费谁出？", "intent": "service",
     "tools": ["search_knowledge_base"],
     "dims": [("退货", ["退货", "天"]), ("运费", ["运费", "邮费"])]},
    {"goal": "满多少钱可以包邮？", "intent": "service",
     "tools": ["search_knowledge_base"],
     "dims": [("包邮", ["包邮", "元"])]},
    {"goal": "跨境订单清关大概要多久", "intent": "service",
     "tools": ["search_knowledge_base"],
     "dims": [("清关", ["清关", "工作日", "跨境"])]},
    {"goal": "秒杀抢到的商品还能退货吗", "intent": "service",
     "tools": ["search_knowledge_base"],
     "dims": [("退货", ["退货", "无理由"])]},
    {"goal": "SVIP 比 VIP 多什么权益，怎么升级", "intent": "service",
     "tools": ["search_knowledge_base"],
     "dims": [("权益", ["SVIP", "权益", "积分"])]},
    # ---------- document：上传文档 + 落盘 ----------
    {"goal": "把我刚上传的周报精简成要点", "intent": "document", "doc": DOC_SAMPLE,
     "tools": ["optimize_document"],
     "dims": [("要点", ["要点", "精简", "总结"])]},
    {"goal": "把上面的结论存成一份 md 文件", "intent": "document", "doc": DOC_SAMPLE,
     "tools": ["write_document"],
     "dims": [("存档", ["md", "保存", "已保存", "路径"])]},
    {"goal": "把这份周报内容转成 docx 保存", "intent": "document", "doc": DOC_SAMPLE,
     "tools": ["write_document"],
     "dims": [("docx", ["docx", "保存", "已保存", "路径"])]},
]


# ==================== 二、判据（纯函数，可离线单测） ====================

def intent_hit(expected: str, actual: str | None) -> bool:
    """意图路由对不对（多 Agent 的第一道分诊，错了整条链路都白跑）"""
    return expected == actual


def tool_stats(expected: list[str], actual: list[str]) -> dict:
    """期望工具集合 vs 实际调用集合

    recall    = 期望里被调到的比例 → "该查的没查"
    precision = 实际调用里属于期望的 → "多调乱调"（例如答售后却去查销售）
    重复调用先去重，否则同一工具调两次会把精确率拉低。
    """
    exp = list(dict.fromkeys(expected))
    act = list(dict.fromkeys(actual))
    hit = [t for t in exp if t in act]
    return {
        "hit": hit,
        "missed": [t for t in exp if t not in act],
        "extra": [t for t in act if t not in exp],
        "recall": len(hit) / len(exp) if exp else 1.0,
        "precision": len([t for t in act if t in exp]) / len(act) if act else 0.0,
    }


def coverage_missing(text: str, dims: list[tuple[str, list[str]]]) -> list[str]:
    """该覆盖的方面里，哪些在证据文本中找不到任何同义关键词"""
    return [name for name, kws in dims if not any(k in text for k in kws)]


def hint_violations(plan: list[dict], valid_names: set[str]) -> list[str]:
    """planner 违反"一条子任务一个合法工具名"契约的子任务 id（空 hint 也算）

    空 hint 会让 executor 放开全集 → 隔离静默失效，所以和非法 hint 一样算违规。
    """
    bad = []
    for t in plan:
        hint = str(t.get("tool_hint") or "").strip()
        if not hint or hint not in valid_names:
            bad.append(str(t.get("id")))
    return bad


def grounding(report: str, evidence: str) -> tuple[int, int, list[str]]:
    """报告数字有据情况：(有据数, 总数字数, 无据数字清单)

    复用 rag_answer_eval.numeric_overflows（子串匹配、刻意偏松）：它只筛不判 ——
    报告自己算出来的数（两数相加）会被误报成"无据"，所以输出的是人工复核清单而不是扣分。
    """
    ungrounded = numeric_overflows(report, evidence)
    total = len(re.findall(r"\d+(?:\.\d+)?", report))
    return total - len(ungrounded), total, ungrounded


# ==================== 三、工具调用录制（重写 subset 是关键） ====================

class RecordingRegistry(ToolRegistry):
    """只加录制的注册中心：记下每个子任务真正调了哪些工具、成功没成功"""

    def __init__(self, calls: list | None = None):
        super().__init__()
        self.calls: list[dict] = calls if calls is not None else []

    def subset(self, names) -> "RecordingRegistry":
        sub = RecordingRegistry(self.calls)      # 共享同一个调用日志
        for n in names:
            tool = self.tools.get(n)
            if tool is not None:
                sub.register(tool)
        return sub

    async def execute(self, name: str, **kwargs):
        result = await super().execute(name, **kwargs)
        self.calls.append({"name": name, "ok": bool(result.success),
                           "error": (result.error or "")[:120]})
        return result


# ==================== 四、dry-run 假 LLM（验证评测管线，不花钱） ====================

# 假 LLM 造参数用：每个工具一套合法参数（registry 会做必填/类型校验，写错就算调用失败）
_TOOL_ARGS = {
    "query_sales": {"start_date": "2026-09-07", "end_date": "2026-09-13"},
    "category_query": {"category": "数码", "start_date": "2026-09-07", "end_date": "2026-09-13"},
    "check_stock": {"threshold": 50},
    "product_query": {"keyword": "充电宝"},
    "user_profile": {},
    "title_optimizer": {"product_name": "苹果15", "keywords": "超薄,快充", "platform": "淘宝"},
    "copy_generator": {"product_name": "保温杯", "selling_points": "保温12小时", "platform": "小红书"},
    "search_knowledge_base": {"query": "退货 运费"},
    "optimize_document": {"content": "周报原文", "instruction": "精简成要点"},
    "write_document": {"filename": "周报", "content": "周报要点", "format": "md"},
}

_SINGLE_ROLE_MARKERS = ("内容创作专员", "售后客服", "文档专员")


class ScriptedLLM(BaseLLM):
    """dry-run 假 LLM：按 prompt 特征返回标准答案，验证管线而不联网

    注意：dry-run 省的是**对话 token**，不是"完全离线" —— 工具仍然真执行
    （知识库检索会调 embedding/rerank API，SQLite 只读查询照跑）。

    同时**故意注入两处缺陷**，否则"全绿"可能只是判据没生效：
      - flaw_tool 的那条少调一个工具（工具召回率必须 < 100%）
      - flaw_number 的那条报告里多一个编造数字（数字有据率必须 < 100%）

    判断逻辑全部**无状态**（只看最后一条消息是不是 tool 结果），
    因为一个 case 里有多个子任务共用同一个 LLM 实例，用计数器会串台。
    """

    def __init__(self, case: dict, flaw_tool: bool = False, flaw_number: bool = False):
        self.case = case
        self.flaw_tool = flaw_tool
        self.flaw_number = flaw_number

    def _conclusion(self) -> str:
        dims = "、".join(name for name, _ in self.case["dims"])
        return f"已核实：{dims}。本周销售额 8200 元，充电宝库存 300 件。"

    def _plan_json(self) -> str:
        tools = list(self.case["tools"])
        if self.flaw_tool and len(tools) > 1:
            tools = tools[:-1]                    # 注入缺陷：少调一个工具
        plan = [{"id": f"t{i + 1}", "task": f"调查 {tool} 对应的数据", "tool_hint": tool}
                for i, tool in enumerate(tools)]
        return json.dumps(plan, ensure_ascii=False)

    def _tool_call(self, name: str) -> LLMResponse:
        return LLMResponse(content="", tool_calls=[
            ToolCall(id=f"call_{name}", name=name, arguments=_TOOL_ARGS[name])])

    async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
        system = messages[0].content if messages else ""
        is_tool_result = messages[-1].role == "tool"

        if "意图分类器" in system:
            return LLMResponse(content=json.dumps({"intent": self.case["intent"]}, ensure_ascii=False))
        if "任务规划器" in system:
            return LLMResponse(content=self._plan_json())
        if "子任务执行者" in system:
            m = re.search(r"建议优先使用】(\w+)", system)
            if not is_tool_result and m and m.group(1) in _TOOL_ARGS:
                return self._tool_call(m.group(1))
            return LLMResponse(content=self._conclusion())
        if any(r in system for r in _SINGLE_ROLE_MARKERS):
            want = (self.case.get("tools") or [None])[0]
            if not is_tool_result and want in _TOOL_ARGS and want in system:
                return self._tool_call(want)
            return LLMResponse(content=self._conclusion())
        return LLMResponse(content=self._conclusion())

    async def chat_stream(self, messages, tools=None, temperature=0.7):
        """synthesizer 走流式：模拟打字机输出，末尾可选注入一个编造的数字"""
        text = (f"【结论】{self.case['goal']}：本周销售额 8200 元，库存 300 件，建议优先补货。")
        if self.flaw_number:
            text += "\n另发现一笔 99999 元的异常波动。"
        for i in range(0, len(text), 24):
            yield text[i:i + 24]


# ==================== 五、跑一条 case ====================

def _evidence(final: dict) -> str:
    """判覆盖/判数字有据用的证据文本

    分析链路：各子任务结论（synthesizer 拿到的原料）；单角色链路没有子任务，
    用最终回答本身当证据（它本来就是唯一输出）。
    """
    results = final.get("results") or {}
    if results:
        return "\n".join(str(v) for v in results.values())
    return str(final.get("report") or "")


async def run_case(idx: int, case: dict, dry: bool) -> dict:
    budget = UsageBudget(max_tokens=0, max_seconds=0)     # 0 = 不限制，评测要跑完整流程
    inner = (ScriptedLLM(case, flaw_tool=(idx == 1), flaw_number=(idx == 2))
             if dry else create_llm())
    llm = BudgetedLLM(inner, budget)
    registry = RecordingRegistry()
    register_all_tools(registry, llm, context={"user_id": 0, "session_id": "eval"})

    events: list[dict] = []

    async def on_event(evt: dict) -> None:
        events.append(evt)

    graph = build_supervisor(llm, registry, on_event=on_event)
    state = {"goal": case["goal"], "history": "", "user_profile": "",
             "uploaded_data": case.get("doc", "")}

    t0 = time.time()
    try:
        final = await graph.invoke(state)
        error = ""
    except Exception as e:                                # 单条失败不炸整轮（Key 失效/限流很常见）
        final, error = {}, f"{type(e).__name__}: {str(e)[:160]}"
    elapsed = time.time() - t0

    report = str(final.get("report") or "")
    evidence = _evidence(final)
    plan = final.get("plan") or []
    actual_tools = [c["name"] for c in registry.calls]
    ts = tool_stats(case["tools"], actual_tools)
    is_analysis = case["intent"] == "analysis"
    grounded_n, total_n, ungrounded = grounding(report, evidence) if is_analysis else (0, 0, [])

    return {
        "idx": idx, "goal": case["goal"], "expected_intent": case["intent"],
        "actual_intent": final.get("intent"), "error": error,
        "tools": ts, "actual_tools": actual_tools,
        "failed_calls": [c["name"] for c in registry.calls if not c["ok"]],
        "missing_dims": coverage_missing(evidence, case["dims"]),
        "dim_total": len(case["dims"]),
        "hint_bad": hint_violations(plan, set(registry.tools)) if plan else [],
        "plan_size": len(plan),
        "grounded": (grounded_n, total_n), "ungrounded": ungrounded,
        "usage": budget.snapshot(), "elapsed": elapsed,
        "subtask_events": len([e for e in events if e.get("type") == "subtask"]),
    }


# ==================== 六、汇总与输出 ====================

def summarize(rows: list[dict]) -> dict:
    """把逐条结果汇成指标（整体微平均，避免单条 case 权重被放大的错觉）"""
    ok = [r for r in rows if not r["error"]]
    n = len(ok) or 1
    exp_tools = sum(len(r["tools"]["hit"]) + len(r["tools"]["missed"]) for r in ok)
    act_tools = sum(len(dict.fromkeys(r["actual_tools"])) for r in ok)
    plans = [r for r in ok if r["plan_size"]]
    return {
        "ran": len(ok), "failed": len(rows) - len(ok),
        "intent_acc": sum(intent_hit(r["expected_intent"], r["actual_intent"]) for r in ok) / n,
        "tool_recall": (sum(len(r["tools"]["hit"]) for r in ok) / exp_tools) if exp_tools else 1.0,
        "tool_precision": (sum(len(r["tools"]["hit"]) for r in ok) / act_tools) if act_tools else 0.0,
        "dims": sum(r["dim_total"] for r in ok),
        "dims_missing": sum(len(r["missing_dims"]) for r in ok),
        "hint_bad": sum(len(r["hint_bad"]) for r in plans),
        "plan_items": sum(r["plan_size"] for r in plans),
        "grounded": sum(r["grounded"][0] for r in ok),
        "numbers": sum(r["grounded"][1] for r in ok),
        "ungrounded": [(r["goal"], r["ungrounded"]) for r in ok if r["ungrounded"]],
        "failed_calls": sum(len(r["failed_calls"]) for r in ok),
        "calls": sum(r["usage"]["calls"] for r in ok),
        "tokens": sum(r["usage"]["total_tokens"] for r in ok),
        "elapsed": sum(r["elapsed"] for r in ok),
    }


async def main() -> None:
    dry = "--dry-run" in sys.argv
    cases = list(enumerate(GOLDEN, 1))
    if "--all" not in sys.argv:
        limit = 5
        for i, a in enumerate(sys.argv):
            if a == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        step = max(1, len(cases) // limit)
        cases = cases[::step][:limit]

    EVAL_OUTPUTS.mkdir(exist_ok=True)
    doc_output.OUTPUTS_DIR = EVAL_OUTPUTS         # 落盘沙箱：不污染真实 outputs/
    print(f"多 Agent 评测：{len(cases)} 条"
          + ("　【DRY-RUN：假 LLM，只验证评测管线，不是真实成绩】" if dry else "　【真 LLM】") + "\n")

    rows = []
    for idx, case in cases:
        row = await run_case(idx, case, dry)
        rows.append(row)
        ts = row["tools"]
        flag = "✓" if intent_hit(case["intent"], row["actual_intent"]) else "✗"
        print(f"{idx:>2}. {flag} 意图={str(row['actual_intent']):<9}"
              f" 工具 {len(ts['hit'])}/{len(ts['hit']) + len(ts['missed'])}"
              f" 缺维度={row['missing_dims'] or '无'}"
              f" 数字有据={row['grounded'][0]}/{row['grounded'][1]}"
              f" {row['usage']['calls']}次调用/{row['elapsed']:.1f}s")
        if ts["missed"]:
            print(f"      ⚠ 没调到的期望工具：{ts['missed']}")
        if ts["extra"]:
            print(f"      ⚠ 多调的工具：{ts['extra']}")
        if row["hint_bad"]:
            print(f"      ⚠ hint 违规子任务：{row['hint_bad']}")
        if row["failed_calls"]:
            print(f"      ⚠ 工具调用失败：{row['failed_calls']}")
        if row["ungrounded"]:
            print(f"      ⚠ 报告里无据的数字（待人工复核）：{row['ungrounded']}")
        if row["error"]:
            print(f"      ✗ 本条失败：{row['error']}")

    s = summarize(rows)
    if s["ran"]:
        print(f"\n{'指标':<22}{'结果':>14}")
        print(f"{'① 意图路由准确率':<22}{s['intent_acc']:>13.0%}")
        print(f"{'② 工具召回率':<22}{s['tool_recall']:>13.0%}")
        print(f"{'   工具精确率':<22}{s['tool_precision']:>13.0%}")
        print(f"{'③ 维度覆盖率':<22}{(1 - s['dims_missing'] / max(1, s['dims'])):>13.0%}"
              f"  ({s['dims'] - s['dims_missing']}/{s['dims']} 个维度)")
        print(f"{'④ hint 合规率':<22}{(1 - s['hint_bad'] / max(1, s['plan_items'])):>13.0%}"
              f"  ({s['plan_items'] - s['hint_bad']}/{s['plan_items']} 条子任务)")
        print(f"{'⑤ 报告数字有据率':<22}{(s['grounded'] / s['numbers'] if s['numbers'] else 1):>13.0%}"
              f"  ({s['grounded']}/{s['numbers']}，仅分析链路)")
        print(f"{'⑥ 总调用 / token / 耗时':<22}{s['calls']:>7} 次 / {s['tokens']:>7} tokens / "
              f"{s['elapsed']:.0f}s（平均 {s['calls'] / s['ran']:.1f} 次、{s['elapsed'] / s['ran']:.1f}s 每轮）")
        if s["failed_calls"]:
            print(f"   工具调用失败总数：{s['failed_calls']}")
        if s["ungrounded"]:
            print("\n需要人工复核的「无据数字」（筛查不是判罪：报告自己算出来的数会被误报）：")
            for goal, nums in s["ungrounded"][:5]:
                print(f"  · {goal} → {nums}")
    if s["failed"]:
        print(f"\n另有 {s['failed']} 条整体失败（指标只按跑成功的 {s['ran']} 条算）")

    # 显式关闭 qdrant 客户端：本地模式是单进程锁，靠解释器析构释放会留下"已被占用"的坑
    try:
        from backend.infrastructure.vector_store.qdrant_client import get_client
        get_client().close()
    except Exception:
        pass

    shutil.rmtree(EVAL_OUTPUTS, ignore_errors=True)
    print("\n怎么读：② 召回率低 = 拆解/工具选择漏了必要维度；④ 低 = planner 没给有效的首选工具提示；"
          "\n⑤ 低 = 报告在编数（编排层幻觉）。改 prompt 前后各跑一次，对比同一份金标集。")


if __name__ == "__main__":
    asyncio.run(main())
