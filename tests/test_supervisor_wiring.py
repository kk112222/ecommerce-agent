"""supervisor 图的接线（离线：结构断言 + router 映射 + 一条端到端落盘链路）

结构部分不需要 LLM：build_supervisor 只做对象组装，条件边是同步 router，可直接拿 state 调用。
最后一条端到端用例走假 LLM 脚本，验证"落盘发生在报告产出之后"这个顺序。
"""
import asyncio
import logging
from datetime import datetime

from backend.agents.supervisor import build_supervisor
from backend.core.tool.base import ToolResult
from backend.core.tool.registry import ToolRegistry
from backend.core.llm.base import LLMResponse
from tests.conftest import FakeLLM


def _graph():
    return build_supervisor(llm=None, registry=ToolRegistry())


def _run(coro):
    return asyncio.run(coro)


def test_all_nodes_registered():
    nodes = set(_graph().nodes)
    assert {"intent", "planner", "executor", "synthesize",
            "content", "service", "document", "save"} <= nodes


def test_intent_router_maps_each_intent():
    """四类意图各路由到对应链路；未知/缺失 → 兜底 analysis"""
    route = _graph().conditions["intent"]
    assert route({"intent": "analysis"}) == "planner"
    assert route({"intent": "content"}) == "content"
    assert route({"intent": "service"}) == "service"
    assert route({"intent": "document"}) == "document"
    assert route({}) == "planner"                       # 没分类结果时兜底


def test_entry_point_is_intent():
    assert _graph().entry_point == "intent"


# ==================== 落盘（save）节点 ====================

def _install_recorder(reg: ToolRegistry, result: ToolResult) -> list:
    """把 registry.execute 换成记录器 —— 落盘用例不该真往 outputs/ 写文件"""
    calls: list = []

    async def fake_execute(name, **kwargs):
        calls.append((name, kwargs))
        return result

    reg.execute = fake_execute
    return calls


_OK = ToolResult(success=True, data={"path": "outputs/1/s/经营周报-2026-09-17.md"})


def test_save_router_only_when_skill_declares_save():
    """落盘是 synthesize 的**可选**后置：技能声明 save: 才走，否则图到 None 结束

    依赖 weekly-report.md 真的声明了 save:（这正是要锁住的契约：周报必须落盘）。
    """
    route = _graph().conditions["synthesize"]
    assert route({"skill": "weekly-report"}) == "save"
    assert route({"skill": ""}) is None                 # 没命中技能 → 结束
    assert route({}) is None
    assert route({"skill": "不存在的技能"}) is None


def test_save_node_uses_skill_filename_template():
    """文件名模板来自技能（{date} 替换成今天），扩展名转成工具的 format 参数"""
    reg = ToolRegistry()
    graph = build_supervisor(llm=None, registry=reg)
    calls = _install_recorder(reg, _OK)

    state = _run(graph.nodes["save"]({"skill": "weekly-report", "report": "# 本周经营\n内容"}))

    assert len(calls) == 1
    name, kwargs = calls[0]
    assert name == "write_document"
    assert kwargs["filename"] == f"经营周报-{datetime.now().strftime('%Y-%m-%d')}"
    assert kwargs["format"] == "md", "扩展名要转成 format，不能把 .md 塞进 filename"
    assert kwargs["content"] == "# 本周经营\n内容", "落盘内容就是最终报告原文"
    assert state["saved_file"].endswith(".md")


def test_save_node_skips_without_skill_or_report():
    """没命中技能 / 技能没声明 save / 报告是空的 → 一次工具调用都不该发生"""
    reg = ToolRegistry()
    graph = build_supervisor(llm=None, registry=reg)
    calls = _install_recorder(reg, _OK)

    for state in ({"skill": "", "report": "# 报告"},           # 没命中技能
                  {"skill": "不存在的技能", "report": "# 报告"},
                  {"skill": "weekly-report", "report": ""},    # 报告为空（降级路径）
                  {"skill": "weekly-report", "report": "   "},
                  {"skill": "weekly-report"}):                 # 压根没有 report 键
        _run(graph.nodes["save"](state))

    assert calls == []


def test_save_node_failure_does_not_break_report(caplog):
    """落盘失败只是没文件，报告早就推给前端了 —— 不许抛异常、只留日志"""
    reg = ToolRegistry()
    graph = build_supervisor(llm=None, registry=reg)
    _install_recorder(reg, ToolResult(success=False, data=None, error="磁盘满了"))

    state = {"skill": "weekly-report", "report": "# 报告"}
    with caplog.at_level(logging.WARNING, logger="ecommerce-agent"):
        out = _run(graph.nodes["save"](state))

    assert out["report"] == "# 报告", "报告必须原样保留"
    assert "saved_file" not in out, "没落成就不该留下路径"
    assert any("报告落盘失败" in r.getMessage() for r in caplog.records)


def test_analysis_path_ends_when_no_save():
    """没有技能（最常见的分析请求）→ 图必须正常跑到头，不能被 None 分支噎住

    这条守的是 invoke 的收尾：synthesize 现在挂的是**条件边**（以前它是终点），
    router 返回 None 时 while current 要正常退出。这里错了就是每轮分析全部 500。
    """
    llm = FakeLLM([
        '{"intent": "analysis"}',
        '[{"id": "t1", "task": "查销售", "tool_hint": "query_sales"}]',
        "销售环比下降 8%",
    ])
    final = _run(build_supervisor(llm, ToolRegistry()).invoke({"goal": "这周为什么掉量"}))

    assert final["report"].strip()
    assert "saved_file" not in final, "没命中带 save 的技能，不该有任何落盘"


def test_save_runs_after_report_is_ready():
    """端到端：命中声明了 save: 的技能 → 落盘内容 == 最终报告

    钉的是**顺序**：executor 在 synthesize 之前跑，那时 state["report"] 还不存在。
    落盘若是挂在 executor 上（或任何上游），存下去的只会是空文件 —— 这条用例正是
    "为什么不能给 executor 加 document 范围"的可执行说明。
    """
    reg = ToolRegistry()
    calls = _install_recorder(reg, _OK)
    llm = FakeLLM([
        '{"intent": "analysis"}',
        '{"skill": "weekly-report", "tasks": [{"id": "t1", "task": "查销售", "tool_hint": "query_sales"}]}',
        '{"tasks": [{"id": "t1", "task": "查本周期销售并取上周期做环比", "tool_hint": "query_sales"}]}',
        "销售环比下降 8%",
    ])
    final = _run(build_supervisor(llm, reg).invoke({"goal": "出个经营周报"}))

    assert final["skill"] == "weekly-report"
    assert len(calls) == 1, "命中技能就该落盘一次"
    name, kwargs = calls[0]
    assert name == "write_document"
    assert kwargs["content"] == final["report"].strip(), "存的必须是最新报告，不是空串"
    assert kwargs["content"].strip(), "空文件等于没落盘"


def test_save_node_writes_real_file(tmp_path, monkeypatch):
    """真工具落盘：save_node 拼的参数能一路走到 doc_output 写出文件

    其它用例都 mock 了 registry.execute（不碰磁盘），"参数拼对了、真工具其实接不住"
    这种错就测不出来。这里用**真** WriteDocument，只把 OUTPUTS_DIR 换到 tmp_path。
    """
    import backend.infrastructure.doc_output as doc_output
    from backend.tools import register_all_tools

    monkeypatch.setattr(doc_output, "OUTPUTS_DIR", tmp_path)
    reg = ToolRegistry()
    register_all_tools(reg, FakeLLM(), context={"user_id": 7, "session_id": "s1"})
    graph = build_supervisor(llm=None, registry=reg)

    state = _run(graph.nodes["save"](
        {"skill": "weekly-report", "report": "# 本周经营\n销售额 1 万"}))

    today = datetime.now().strftime("%Y-%m-%d")
    written = tmp_path / "user_7" / "s1" / f"经营周报-{today}.md"
    assert written.exists(), f"没落成：{written}"
    assert "销售额 1 万" in written.read_text(encoding="utf-8")
    assert state["saved_file"].endswith(f"user_7/s1/经营周报-{today}.md"), \
        "state 里留下的路径要给前端能用的相对路径"


# ==================== 落盘的两条边界（2026-09-17） ====================

_ORPHAN = ToolResult(success=True,
                     data={"path": "outputs/经营周报-2026-09-17.md"})   # 没进 user_<id>/


def test_save_node_warns_when_file_lands_outside_user_dir(caplog):
    """回归：工具 context 缺 user_id/session_id 时文件会落到 outputs 根目录 —— 必须告警

    这条路径是实测出来的（scripts/cli.py:39 就是 register_all_tools(registry, llm)，不带 context）：
    文件确实写出来了，但 list_documents / resolve_user_file 都以 outputs/user_<id>/ 为根，
    于是"报告存了、谁都下不到"，而日志里只有一句 INFO「报告已落盘」—— 完全看不出问题。
    """
    reg = ToolRegistry()
    graph = build_supervisor(llm=None, registry=reg)
    _install_recorder(reg, _ORPHAN)

    with caplog.at_level(logging.WARNING, logger="ecommerce-agent"):
        out = _run(graph.nodes["save"]({"skill": "weekly-report", "report": "# 报告"}))

    assert out["saved_file"] == "outputs/经营周报-2026-09-17.md"
    assert any("非用户目录" in r.getMessage() for r in caplog.records), "落到用户目录外必须告警，否则是静默失败"


def test_save_node_skips_degraded_report(caplog):
    """降级/兜底报告不落盘：那不是「经营周报」，是残件

    synthesize 因预算或异常走兜底时，产出的是 _fallback_report（开头写着"本轮因预算限制提前结束"）。
    把它存成「经营周报-<date>.md」比不落盘更糟：交付物里混进残件，而且外人看不出来。
    """
    reg = ToolRegistry()
    graph = build_supervisor(llm=None, registry=reg)
    calls = _install_recorder(reg, _OK)

    with caplog.at_level(logging.WARNING, logger="ecommerce-agent"):
        out = _run(graph.nodes["save"]({"skill": "weekly-report",
                                       "report": "⚠️ 本轮分析因预算限制提前结束，以下是已完成的调查结果……",
                                       "report_degraded": True}))

    assert calls == [], "降级报告不该落盘"
    assert "saved_file" not in out
    assert any("降级" in r.getMessage() for r in caplog.records)

