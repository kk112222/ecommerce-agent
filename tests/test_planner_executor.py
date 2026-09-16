"""Planner 输出校验 + Executor 工具范围（离线）

这两块都是"LLM 输出不可信"的典型现场：
- Planner：LLM 给的 JSON 形状千奇百怪，以前只判 isinstance(list)，id 重复会把两条子任务结果
  折叠成一条（静默丢结果），缺 task 直接 KeyError 崩掉整轮。
- Executor：能力边界从"单条子任务"上移到"角色"（2026-09-16 调整）。
  旧做法是按 planner 的 tool_hint 把注册中心收窄成"只含那一个工具"：省 token、硬隔离，
  但子任务的语义是"回答一个问题"，它天然可能要多工具（查销售 → 再查库存），
  拆解不准时就会**静默给出残缺结论**。新做法：子任务拿 analysis 角色的 5 个数据工具，
  tool_hint 降级成"建议优先使用"的提示；跨角色越界（分析任务想写文件）仍被 schema 挡住。
下面钉的就是这条边界：同角色可多工具、跨角色必须拦住。
"""
import json
import logging

import pytest

from backend.agents.executor import Executor
from backend.agents.planner import Planner
from backend.core.llm.base import BaseLLM, LLMResponse, ToolCall
from backend.core.tool.registry import TOOL_SCOPES, ToolRegistry
from backend.tools import register_all_tools

ANALYSIS_SCOPE = TOOL_SCOPES["analysis"]


class _RecordingLLM(BaseLLM):
    """记录每次 chat 收到的 messages 与 tools，用来断言给了哪些工具/上下文"""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.tools_seen: list = []
        self.last_messages: list = []

    async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
        self.tools_seen.append(tools)
        self.last_messages = list(messages)
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="（子任务结论）")

    async def chat_stream(self, messages, tools=None, temperature=0.7):
        yield ""


def _tool_call(name: str, **args) -> LLMResponse:
    """造一次工具调用的 LLM 响应（ReAct 循环的第一轮）"""
    return LLMResponse(content="",
                       tool_calls=[ToolCall(id=f"call_{name}", name=name, arguments=args)])


def _registry():
    reg = ToolRegistry()
    register_all_tools(reg, _RecordingLLM())
    return reg


def _names(tools) -> list[str]:
    return sorted(t["function"]["name"] for t in (tools or []))


def _plan(payload) -> list[dict]:
    """拿一段 LLM 输出喂给 Planner，返回清洗后的计划"""
    llm = _RecordingLLM([LLMResponse(content=payload if isinstance(payload, str)
                                     else json.dumps(payload, ensure_ascii=False))])
    return _run(Planner(llm, _registry()).plan("分析本周销售"))


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ==================== Planner ====================

def test_non_list_output_falls_back_to_single_task():
    """非 JSON / 非数组 → 降级成单任务，且 id 是 t1（不是以前那个笔误的 "tl"）"""
    for bad in ("这不是 JSON", '{"id":"t1"}', "[]", "null"):
        plan = _plan(bad)
        assert plan == [{"id": "t1", "task": "分析本周销售", "tool_hint": ""}], bad


def test_duplicate_ids_are_renumbered():
    """id 重复会让下游 dict 把两条结果折叠成一条 → 必须重编号保证唯一"""
    plan = _plan([
        {"id": "t1", "task": "查销售额", "tool_hint": "query_sales"},
        {"id": "t1", "task": "查库存", "tool_hint": "check_stock"},
    ])
    ids = [p["id"] for p in plan]
    assert len(set(ids)) == 2 and len(plan) == 2


def test_duplicate_id_whose_replacement_is_taken_terminates():
    """回归：替换候选名也被占用时必须继续往前找，不能原地自旋

    上面的 [t1, t1] 恰好让候选 t2 空着，一次就过 —— 触发条件是"重复的那条 id，
    它算出来的替换候选也已经在 seen_ids 里"，例如 [t1, t3, t3]（LLM 完全可能这么给）。
    老写法在循环体内用 len(seen_ids) 当候选号，len() 恒定 → 候选名固定 →
    死循环 + 同步 CPU 自旋，堵的不是一个请求而是整个事件循环（faulthandler 已实锤）。
    """
    plan = _plan([
        {"id": "t1", "task": "查销售额", "tool_hint": "query_sales"},
        {"id": "t3", "task": "查库存", "tool_hint": "check_stock"},
        {"id": "t3", "task": "查竞品", "tool_hint": ""},
    ])
    ids = [p["id"] for p in plan]
    assert len(plan) == 3, "三条子任务都要留下，不能丢项"
    assert len(set(ids)) == 3, f"id 必须唯一（会折叠下游 results），实际 {ids}"


def test_missing_task_dropped_and_fabricated_hint_cleared():
    """缺 task 的条目丢弃；编造的工具名清空（否则模型会看到一条不存在的工具提示）"""
    plan = _plan([
        {"id": "t1", "task": "查销售额", "tool_hint": "不存在的工具"},
        {"id": "t2", "tool_hint": "query_sales"},              # 没有 task
    ])
    assert plan == [{"id": "t1", "task": "查销售额", "tool_hint": ""}]


def test_plan_prompt_marks_hint_as_hint_not_permission():
    """回归：prompt 必须把 tool_hint 说成"首选工具提示"，且明确"子任务可多工具"

    这条是 prompt-only 改动的唯一自动防线。写成"只允许填一个工具名…需要多工具必须拆条"
    时，模型会为了守规则把本该一条的交叉验证拆碎（或者反过来，拆得不够细时子任务被锁死）。
    现在的契约：hint 只是起点提示、不是权限；子任务内部可以调多个工具。
    """
    llm = _RecordingLLM([LLMResponse(content="[]")])
    _run(Planner(llm, _registry()).plan("这周为什么掉量"))

    prompt = llm.last_messages[0].content
    assert "首选工具" in prompt
    assert "不是权限边界" in prompt
    assert "调用多个工具" in prompt


def test_multi_tool_hint_is_cleared_and_logged(caplog):
    """一条子任务填多个工具名 → 清空 + 告警（首选工具只该有一个）

    注意这里清空**不再**影响能力（子任务拿的是角色范围），清的是"提示质量"：
    脏值会让模型看到一句无意义的提示，也让 planner 的守约频次失去观测点。
    """
    with caplog.at_level(logging.WARNING, logger="ecommerce-agent"):
        plan = _plan([
            {"id": "t1", "task": "为什么销量跌", "tool_hint": "query_sales,check_stock"},
            {"id": "t2", "task": "查竞品价格", "tool_hint": "query_sales"},
        ])

    assert plan[0]["tool_hint"] == ""               # 多工具 hint 清空
    assert plan[1]["tool_hint"] == "query_sales"    # 单个合法工具名照旧生效，别误伤
    assert any("tool_hint 不是单个合法工具名" in r.getMessage() for r in caplog.records)


def test_plan_is_capped():
    """prompt 要求 2~5 条，LLM 给 9 条 → 截断，别把成本放大到失控"""
    plan = _plan([{"id": f"t{i}", "task": f"子任务{i}", "tool_hint": ""} for i in range(1, 10)])
    assert len(plan) == Planner.MAX_TASKS


# ==================== Executor：能力边界在"角色"上 ====================

def test_executor_gets_analysis_role_scope():
    """子任务拿到的是 analysis 角色的 5 个数据工具，而不是"只含 hint 那一个"

    旧行为（单工具硬隔离）会让"为什么销量跌"这种需要交叉验证的子任务只查一面，
    而且不报错 —— 这是这次调整的直接原因。
    """
    llm = _RecordingLLM()
    ex = Executor(llm, _registry())
    _run(ex.run({"id": "t1", "task": "查本月销售额", "tool_hint": "query_sales"}))

    assert _names(llm.tools_seen[0]) == sorted(ANALYSIS_SCOPE)
    assert len(ANALYSIS_SCOPE) > 1, "角色范围本身必须多于一个工具，否则这条用例没意义"
    # hint 仍在 prompt 里，但语义是"建议优先使用"
    assert "【建议优先使用】query_sales" in llm.last_messages[0].content


def test_executor_uses_role_scope_without_hint():
    """没给 hint → 仍是角色范围（不是全集、也不是被锁死）"""
    llm = _RecordingLLM()
    ex = Executor(llm, _registry())
    _run(ex.run({"id": "t1", "task": "随便看看", "tool_hint": ""}))

    full = len(_registry().tools)
    assert _names(llm.tools_seen[0]) == sorted(ANALYSIS_SCOPE)
    assert len(ANALYSIS_SCOPE) < full, "角色范围应当小于全集（否则隔离没生效）"


def test_executor_allows_multiple_tools_in_one_subtask():
    """同角色内可以连续调不同工具（这是这次调整的核心能力）

    脚本：先查销售 → 再查库存 → 给结论。旧实现第二步会被挡住（schema 里只有 query_sales），
    新实现两次调用都进得了 ReAct 循环。
    """
    llm = _RecordingLLM([
        _tool_call("query_sales", start_date="2026-09-01", end_date="2026-09-07"),
        _tool_call("check_stock", threshold=50),
        LLMResponse(content="销售额下滑且库存告急"),
    ])
    ex = Executor(llm, _registry())
    result = _run(ex.run({"id": "t1", "task": "为什么销量跌", "tool_hint": "query_sales"}))

    tool_msgs = [m for m in llm.last_messages if m.role == "tool"]
    assert result == "销售额下滑且库存告急"
    assert len(tool_msgs) == 2, "两个不同工具都要真的进到循环里执行"
    assert all(m.content for m in tool_msgs), "两格都要有反馈（数据或可读错误），不能是空"


def test_executor_ignores_invalid_hint_with_warning(caplog):
    """非法 hint 只影响提示，不影响权限：范围照旧、日志留痕"""
    llm = _RecordingLLM()
    ex = Executor(llm, _registry())
    with caplog.at_level(logging.WARNING, logger="ecommerce-agent"):
        _run(ex.run({"id": "t1", "task": "为什么销量跌", "tool_hint": "query_sales,check_stock"}))

    assert _names(llm.tools_seen[0]) == sorted(ANALYSIS_SCOPE)
    assert "【建议优先使用】" not in llm.last_messages[0].content   # 脏 hint 不该进 prompt
    assert any("忽略该提示" in r.getMessage() for r in caplog.records)


def test_cross_role_tool_is_structurally_blocked():
    """跨角色越界必须被拦住：分析子任务不能写文件（schema 里没有这个工具）"""
    sub = _registry().subset_scope("analysis")
    res = _run(sub.execute("write_document", filename="报告", content="内容"))

    assert res.success is False
    assert "没有名为 write_document 的工具" in (res.error or "")
    assert "query_sales" in (res.error or ""), "错误里要告诉它可用工具有哪些"


def test_unknown_scope_falls_back_to_full_registry(caplog):
    """范围名写错 → 放开全集，但必须告警（静默降级等于没人查得到）"""
    reg = _registry()
    with caplog.at_level(logging.WARNING, logger="ecommerce-agent"):
        sub = reg.subset_scope("不存在的角色")

    assert len(sub.tools) == len(reg.tools)
    assert any("为空或工具未注册" in r.getMessage() for r in caplog.records)


def test_executor_injects_uploaded_data():
    """竞品数据要真的进到子任务 prompt 里（P1-6 的断链）"""
    llm = _RecordingLLM()
    ex = Executor(llm, _registry())
    _run(ex.run({"id": "t1", "task": "对比竞品价格", "tool_hint": ""},
                uploaded_data="我方 T恤 99，对手 79", user_profile="负责男装类目"))

    system_text = llm.last_messages[0].content
    assert "我方 T恤 99" in system_text and "负责男装类目" in system_text
