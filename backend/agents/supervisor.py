import asyncio
from backend.core.agent.base import AgentGraph
from backend.agents.intent_classifier import IntentClassifier
from backend.agents.planner import Planner
from backend.agents.executor import Executor
from backend.agents.synthesizer import Synthesizer
from backend.agents.content_gen.content_agent import ContentAgent
from backend.agents.customer_service.service_agent import ServiceAgent
from backend.agents.document.document_agent import DocumentAgent


def build_supervisor(llm, registry, on_event=None) -> AgentGraph:
    """on_event: 可选回调 async (event: dict) -> None，图执行时主动汇报进度

    图结构：intent(意图分类) → 条件边 →
        分析类: planner → executor(并行) → synthesize
        内容类: content(ReAct 生成文案/标题)
        客服类: service(ReAct 查知识库)
        文档类: document(ReAct 解析/优化/生成落盘)
    """
    classifier = IntentClassifier(llm)
    planner = Planner(llm, registry)
    executor = Executor(llm, registry)
    synthesizer = Synthesizer(llm, registry)
    # 角色 Agent：人格 prompt 和上下文组装收在各自文件里（content_gen / customer_service），
    # supervisor 只负责"什么时候派给谁"，不关心角色内部怎么写
    content_agent = ContentAgent(llm, registry)
    service_agent = ServiceAgent(llm, registry)
    document_agent = DocumentAgent(llm, registry)

    # ============ 意图分类节点（所有问题的第一站） ============
    async def intent_node(state):
        # 先判断意图存进 state["intent"]，后面条件边的 router 只读它
        # （router 必须是同步函数，不能在里面 await LLM）
        state["intent"] = await classifier.classify(state["goal"])
        if on_event:
            await on_event({"type": "intent", "intent": state["intent"]})
        return state

    # ============ 数据分析链路 ============
    async def planner_node(state):
        state["plan"] = await planner.plan(
            state["goal"],
            history=state.get("history", ""),
            user_profile=state.get("user_profile", ""),
            uploaded_data=state.get("uploaded_data", ""),
        )
        if on_event:
            await on_event({"type": "plan", "plan": state["plan"]})
        return state

    async def executor_node(state):
        plan = state["plan"]
        async def run_one(t):
            r = await executor.run(t)
            if on_event:
                await on_event({"type": "subtask", "id": t["id"], "task": t["task"], "result": r})
            return r
        results_list = await asyncio.gather(*[run_one(t) for t in plan])
        state["results"] = {t["id"]: r for t, r in zip(plan, results_list)}
        return state

    async def synthesize_node(state):
        # 流式生成报告：逐 token 推给前端（打字机效果），收尾再推完整报告
        full = ""
        async for chunk in synthesizer.synthesize_stream(
            state["goal"], state["results"],
            history=state.get("history", ""),
            user_profile=state.get("user_profile", ""),
            uploaded_data=state.get("uploaded_data", ""),
        ):
            full += chunk
            if on_event:
                await on_event({"type": "token", "content": chunk})
        state["report"] = full
        if on_event:
            await on_event({"type": "report", "report": full})
        return state

    # ============ 内容生成链路（文案/标题，不拆子任务） ============
    # 注意：角色知识（人格/边界/上下文怎么拼）已收进 ContentAgent（content_gen/content_agent.py），
    # 这里只做调度：跑角色 → 结果放 state → 推 report 事件
    async def content_node(state):
        state["report"] = await content_agent.run(
            goal=state["goal"],
            history=state.get("history", ""),
            user_profile=state.get("user_profile", ""),
        )
        if on_event:
            await on_event({"type": "report", "report": state["report"]})
        return state

    # ============ 客服问答链路（查知识库） ============
    async def service_node(state):
        state["report"] = await service_agent.run(
            goal=state["goal"],
            history=state.get("history", ""),
            user_profile=state.get("user_profile", ""),
        )
        if on_event:
            await on_event({"type": "report", "report": state["report"]})
        return state

    # ============ 文档处理链路（解析/优化/生成落盘） ============
    async def document_node(state):
        # 比其它角色多传 uploaded_data：文档原料来自用户上传（state 里已解析好的文本）
        state["report"] = await document_agent.run(
            goal=state["goal"],
            history=state.get("history", ""),
            user_profile=state.get("user_profile", ""),
            uploaded_data=state.get("uploaded_data", ""),
        )
        if on_event:
            await on_event({"type": "report", "report": state["report"]})
        return state

    # ============ 组装图 ============
    graph = AgentGraph()
    graph.add_node("intent", intent_node)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("content", content_node)
    graph.add_node("service", service_node)
    graph.add_node("document", document_node)

    # 数据分析链路：串行
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "synthesize")

    # 条件边：intent 跑完后，根据 state["intent"] 选链路（router 是同步函数）
    def intent_router(state):
        return state.get("intent", "analysis")
    graph.add_condition_edges("intent", intent_router, {
        "analysis": "planner",
        "content": "content",
        "service": "service",
        "document": "document",
    })

    graph.entry_point = "intent"
    return graph
