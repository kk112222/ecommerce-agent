import asyncio
from backend.core.agent.base import AgentGraph
from backend.core.llm.base import Message
from backend.agents.intent_classifier import IntentClassifier
from backend.agents.planner import Planner
from backend.agents.executor import Executor
from backend.agents.synthesizer import Synthesizer
from backend.agents.data_analysis.simple_agent import ReActAgent


def build_supervisor(llm, registry, on_event=None) -> AgentGraph:
    """on_event: 可选回调 async (event: dict) -> None，图执行时主动汇报进度

    图结构：intent(意图分类) → 条件边 →
        分析类: planner → executor(并行) → synthesize
        内容类: content(ReAct 生成文案/标题)
        客服类: service(ReAct 查知识库)
    """
    classifier = IntentClassifier(llm)
    planner = Planner(llm, registry)
    executor = Executor(llm, registry)
    synthesizer = Synthesizer(llm, registry)

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
    async def content_node(state):
        system_prompt = """你是电商内容创作专员。你只负责根据用户要求生成商品文案/标题，不要分析经营数据。
可用工具：
- title_optimizer：优化/生成商品标题
- copy_generator：生成商品文案/营销话术
只调用内容类工具，不要查销售库存数据。直接产出最终文案。"""
        agent = ReActAgent(llm, registry)
        messages = [Message(role="system", content=system_prompt)]
        if state.get("history"):
            messages.append(Message(role="system", content=f"用户之前的对话：\n{state['history']}"))
        if state.get("user_profile"):
            messages.append(Message(role="system", content=f"用户画像：\n{state['user_profile']}"))
        messages.append(Message(role="user", content=state["goal"]))
        result = await agent.run(messages)
        state["report"] = result
        if on_event:
            await on_event({"type": "report", "report": result})
        return state

    # ============ 客服问答链路（查知识库） ============
    async def service_node(state):
        system_prompt = """你是电商售后客服。用户问的是售后政策、退货规则等问题，需要查知识库才能准确回答。
可用工具：
- search_knowledge_base：检索客服知识库
先检索知识库拿到政策原文，再据此回答。如果知识库里没有答案，如实说"知识库没有查到"，不要编造。"""
        agent = ReActAgent(llm, registry)
        messages = [Message(role="system", content=system_prompt)]
        if state.get("history"):  # 历史非空才插
            messages.append(Message(role="system",
                                    content=f"用户之前的对话：\n{state['history']}"))
        if state.get("user_profile"):
            messages.append(Message(role="system", content=f"用户画像：\n{state['user_profile']}"))
        messages.append(Message(role="user", content=state["goal"]))
        result = await agent.run(messages)
        state["report"] = result
        if on_event:
            await on_event({"type": "report", "report": result})
        return state

    # ============ 组装图 ============
    graph = AgentGraph()
    graph.add_node("intent", intent_node)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("content", content_node)
    graph.add_node("service", service_node)

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
    })

    graph.entry_point = "intent"
    return graph
