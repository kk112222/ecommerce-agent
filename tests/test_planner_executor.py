"""Planner 输出校验 + Executor 子任务隔离（离线）

这两块以前零覆盖，且都是"LLM 输出不可信"的典型现场：
- Planner：LLM 给的 JSON 形状千奇百怪，以前只判 isinstance(list)，id 重复会把两条子任务结果
  折叠成一条（静默丢结果），缺 task 直接 KeyError 崩掉整轮。
- Executor：tool_hint 是死字段、4 个并行子任务各揣全部工具；uploaded_data 没传进去。
"""
import json

import pytest

from backend.agents.executor import Executor
from backend.agents.planner import Planner
from backend.core.llm.base import BaseLLM, LLMResponse
from backend.core.tool.registry import ToolRegistry
from backend.tools import register_all_tools


class _RecordingLLM(BaseLLM):
    """记下每次 chat 收到的 messages 和 tools，用来断言"给了什么工具/上下文" """

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


def _registry():
    reg = ToolRegistry()
    register_all_tools(reg, _RecordingLLM())
    return reg


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


def test_missing_task_dropped_and_fabricated_hint_cleared():
    """缺 task 的条目丢弃；编造的工具名清空（否则子任务会去调一个不存在的工具）"""
    plan = _plan([
        {"id": "t1", "task": "查销售额", "tool_hint": "不存在的工具"},
        {"id": "t2", "tool_hint": "query_sales"},              # 没有 task
    ])
    assert plan == [{"id": "t1", "task": "查销售额", "tool_hint": ""}]


def test_plan_is_capped():
    """prompt 要求 2~5 条，LLM 给 9 条 → 截断，别把成本放大到失控"""
    plan = _plan([{"id": f"t{i}", "task": f"子任务{i}", "tool_hint": ""} for i in range(1, 10)])
    assert len(plan) == Planner.MAX_TASKS


# ==================== Executor ====================

def test_executor_isolates_tools_by_hint():
    """子任务带 tool_hint → 它的 ReAct 只拿到那一个工具（硬隔离，不是 prompt 软约束）"""
    llm = _RecordingLLM()
    ex = Executor(llm, _registry())
    _run(ex.run({"id": "t1", "task": "查本月销售额", "tool_hint": "query_sales"}))

    passed = llm.tools_seen[0]
    assert passed is not None and [t["function"]["name"] for t in passed] == ["query_sales"]


def test_executor_opens_full_toolset_without_hint():
    """没给 hint（或 hint 无效）→ 放开全集，不能把子任务锁死成啥也干不了"""
    llm = _RecordingLLM()
    ex = Executor(llm, _registry())
    _run(ex.run({"id": "t1", "task": "随便看看", "tool_hint": ""}))
    assert len(llm.tools_seen[0]) == len(_registry().tools)


def test_executor_injects_uploaded_data():
    """竞品数据要真的进到子任务 prompt 里（P1-6 的断链）"""
    llm = _RecordingLLM()
    ex = Executor(llm, _registry())
    _run(ex.run({"id": "t1", "task": "对比竞品价格", "tool_hint": ""},
                uploaded_data="我方 T恤 99，对手 79", user_profile="负责男装类目"))

    system_text = llm.last_messages[0].content
    assert "我方 T恤 99" in system_text and "负责男装类目" in system_text
