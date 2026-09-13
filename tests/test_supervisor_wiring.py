"""supervisor 图的接线（离线，纯结构断言 + router 映射）

不需要 LLM：build_supervisor 只做对象组装；条件边是同步 router，可直接拿 state 调用。
"""
from backend.agents.supervisor import build_supervisor
from backend.core.tool.registry import ToolRegistry


def _graph():
    return build_supervisor(llm=None, registry=ToolRegistry())


def test_all_nodes_registered():
    nodes = set(_graph().nodes)
    assert {"intent", "planner", "executor", "synthesize",
            "content", "service", "document"} <= nodes


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
