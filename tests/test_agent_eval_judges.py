"""多 Agent 评测判据的离线单测（不联网、不花钱）

脚本本身要真 LLM 才能跑（编排链路要调模型），但**判据是纯函数**，不该跟着
"没额度就没人管" —— 先钉住尺子本身准不准，再拿它去量 Agent。
这和 tests/test_rag_answer_eval.py 是同一个思路。
"""
from backend.core.tool.base import BaseTool, ToolResult, ToolSpec
from scripts.agent_eval import (
    GOLDEN, RecordingRegistry, coverage_missing, grounding, hint_violations,
    intent_hit, tool_stats,
)


# ==================== 判据：意图 ====================

def test_intent_hit():
    assert intent_hit("service", "service") is True
    assert intent_hit("service", "analysis") is False
    assert intent_hit("service", None) is False


# ==================== 判据：工具召回 / 精确 ====================

def test_tool_stats_all_hit():
    s = tool_stats(["query_sales", "check_stock"], ["check_stock", "query_sales"])
    assert s["missed"] == [] and s["extra"] == []
    assert s["recall"] == 1.0 and s["precision"] == 1.0


def test_tool_stats_flags_missed_tool():
    """该查库存却没查 —— 多 Agent 最典型的"拆解漏维度"，必须被召回率抓到"""
    s = tool_stats(["query_sales", "check_stock"], ["query_sales"])
    assert s["missed"] == ["check_stock"] and s["recall"] == 0.5


def test_tool_stats_flags_extra_tool():
    """答售后却去查销售 —— 精确率要抓得到"""
    s = tool_stats(["search_knowledge_base"], ["search_knowledge_base", "query_sales"])
    assert s["extra"] == ["query_sales"] and s["precision"] == 0.5


def test_tool_stats_dedupes_repeated_calls():
    """同一工具调两次不该把精确率拉低（去重后只算一个）"""
    s = tool_stats(["query_sales"], ["query_sales", "query_sales"])
    assert s["precision"] == 1.0


def test_tool_stats_no_expectation():
    s = tool_stats([], [])
    assert s["recall"] == 1.0 and s["precision"] == 0.0


# ==================== 判据：维度覆盖 ====================

DIMS = [("销售", ["销售", "营业额"]), ("库存", ["库存", "缺货"])]


def test_coverage_all_present():
    assert coverage_missing("本周销售额 8200 元，库存 300 件", DIMS) == []


def test_coverage_reports_synonym_hit():
    """同义说法也算覆盖（"营业额"命中"销售"这一维）"""
    assert coverage_missing("营业额下滑，缺货严重", DIMS) == []


def test_coverage_reports_missing_dimension():
    """只谈了销售、完全没提库存 → 缺一维（正是"只查一面就下结论"的形态）"""
    assert coverage_missing("本周销售额 8200 元", DIMS) == ["库存"]


# ==================== 判据：planner 契约 ====================

def test_hint_violations_flags_empty_and_fake():
    plan = [{"id": "t1", "tool_hint": "query_sales"},
            {"id": "t2", "tool_hint": ""},                 # 空 hint → executor 放开全集
            {"id": "t3", "tool_hint": "不存在的工具"}]
    assert hint_violations(plan, {"query_sales", "check_stock"}) == ["t2", "t3"]


def test_hint_violations_clean_plan():
    plan = [{"id": "t1", "tool_hint": "query_sales"}]
    assert hint_violations(plan, {"query_sales"}) == []


# ==================== 判据：报告数字有据 ====================

def test_grounding_flags_hallucinated_number():
    """子任务结果里只有 8200/300，报告多出 99999 → 无据（编排层幻觉的典型形态）"""
    g, total, bad = grounding("销售额 8200 元，库存 300 件，另有 99999 元波动",
                              "销售额 8200 元，库存 300 件")
    assert bad == ["99999"] and (g, total) == (2, 3)


def test_grounding_all_backed():
    g, total, bad = grounding("销售额 8200 元", "子任务结论：销售额 8200 元")
    assert bad == [] and g == total == 1


# ==================== 录制器：subset 必须共享日志 ====================

class _EchoTool(BaseTool):
    spec = ToolSpec(name="echo", description="回显", parameters={
        "type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]})

    async def execute(self, x=None, **kwargs):
        return ToolResult(success=True, data={"echo": x})


async def test_recording_registry_records_calls():
    reg = RecordingRegistry()
    reg.register(_EchoTool())
    r = await reg.execute("echo", x="hi")
    assert r.success and reg.calls == [{"name": "echo", "ok": True, "error": ""}]


async def test_recording_registry_subset_shares_log():
    """executor 用 subset([hint]) 收窄工具集 → 子任务的调用必须还记在同一个日志里

    父类 ToolRegistry.subset() 返回的是硬编码的 ToolRegistry，不重写就会把录制器丢掉，
    工具召回率会永远算成 0 —— 这条用例就是钉住那个坑。
    """
    reg = RecordingRegistry()
    reg.register(_EchoTool())
    sub = reg.subset(["echo"])
    assert isinstance(sub, RecordingRegistry)
    await sub.execute("echo", x="sub")
    assert [c["name"] for c in reg.calls] == ["echo"]


async def test_recording_registry_records_failed_call():
    """工具调用失败也要入账（失败率是编排健康度的一部分）"""
    reg = RecordingRegistry()
    reg.register(_EchoTool())
    await reg.execute("echo")            # 缺必填参数 x → ToolResult(success=False)
    assert reg.calls[0]["ok"] is False and reg.calls[0]["error"]


# ==================== 金标集自身的体检 ====================

def test_golden_covers_all_intents():
    assert {c["intent"] for c in GOLDEN} == {"analysis", "content", "service", "document"}


def test_golden_case_shape():
    for c in GOLDEN:
        assert c["goal"].strip() and c["tools"], c
        assert all(isinstance(d, tuple) and len(d) == 2 for d in c["dims"]), c
        # document 链路必须带上传文档，否则测的是"没原料时的兜底话术"而不是文档能力
        if c["intent"] == "document":
            assert c.get("doc")
