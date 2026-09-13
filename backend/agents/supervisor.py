import asyncio
from backend.core.agent.base import AgentGraph
from backend.core.llm.budget import BudgetExceeded
from backend.agents.intent_classifier import IntentClassifier
from backend.agents.planner import Planner
from backend.agents.executor import Executor
from backend.agents.synthesizer import Synthesizer
from backend.agents.content_gen.content_agent import ContentAgent
from backend.agents.customer_service.service_agent import ServiceAgent
from backend.agents.document.document_agent import DocumentAgent


def _budget_note(e: BudgetExceeded) -> str:
    """预算用尽时的降级文案（P2-11）

    预算超了要"降级"不要"崩"：已经把工具结果跑出来了，用户应该拿到这份不完整的报告，
    而不是一个 500 或一句"分析失败"。文案里带上具体原因，前端/日志都能看出是被截断的。
    """
    return f"（本轮预算已用尽：{e.detail}；该步骤提前结束，以下内容是已完成部分）"


def _fallback_report(state) -> str:
    """连综合报告的额度都没了：把子任务结果直接拼给用户，别让这一轮白跑"""
    results = state.get("results") or {}
    body = "\n\n".join(f"【子任务 {tid}】\n{text}" for tid, text in results.items())
    if not body:
        return "⚠️ 本轮预算在产出任何结果前就用尽了，请缩小问题范围后重试。"
    return f"⚠️ 本轮分析因预算限制提前结束，以下是已完成的调查结果（未经综合归纳）：\n\n{body}"


def build_supervisor(llm, registry, on_event=None) -> AgentGraph:
    """on_event: 可选回调 async (event: dict) -> None，图执行时主动汇报进度

    图结构：intent(意图分类) → 条件边 →
        分析类: planner → executor(并行) → synthesize
        内容类: content(ReAct 生成文案/标题)
        客服类: service(ReAct 查知识库)
        文档类: document(ReAct 解析/优化/生成落盘)

    所有 LLM 调用都走同一个 BudgetedLLM（在 route 层包好传进来），所以这里只要接住
    BudgetExceeded 并降级；预算账本由调用方持有，最后统一汇总成 usage 事件。
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
        try:
            state["intent"] = await classifier.classify(state["goal"])
        except BudgetExceeded:
            # 开局就没额度（预算配得太小）：按最通用的分析链路走，后面各节点会依次降级
            state["intent"] = "analysis"
        if on_event:
            await on_event({"type": "intent", "intent": state["intent"]})
        return state

    # ============ 数据分析链路 ============
    async def planner_node(state):
        try:
            state["plan"] = await planner.plan(
                state["goal"],
                history=state.get("history", ""),
                user_profile=state.get("user_profile", ""),
                uploaded_data=state.get("uploaded_data", ""),
            )
        except BudgetExceeded:
            state["plan"] = []          # 没有额度做规划：不猜任务，直接走"无结果"降级报告
        if on_event:
            await on_event({"type": "plan", "plan": state["plan"]})
        return state

    async def executor_node(state):
        plan = state["plan"]
        async def run_one(t):
            # 把上下文透传给子任务：以前只传 task，导致"结合上传数据做竞品对比"的子任务拿不到原料
            try:
                r = await executor.run(t,
                                       uploaded_data=state.get("uploaded_data", ""),
                                       user_profile=state.get("user_profile", ""))
            except BudgetExceeded as e:
                # 一个子任务把额度吃光了，不能连累其余三个：这一格用降级文案占位，
                # 报告结构保持完整，synthesizer 还能把跑完的部分综合出来
                r = _budget_note(e)
            if on_event:
                await on_event({"type": "subtask", "id": t["id"], "task": t["task"], "result": r})
            return r
        results_list = await asyncio.gather(*[run_one(t) for t in plan])
        state["results"] = {t["id"]: r for t, r in zip(plan, results_list)}
        return state

    async def synthesize_node(state):
        # 流式生成报告：逐 token 推给前端（打字机效果），收尾再推完整报告
        full = ""
        try:
            async for chunk in synthesizer.synthesize_stream(
                state["goal"], state["results"],
                history=state.get("history", ""),
                user_profile=state.get("user_profile", ""),
                uploaded_data=state.get("uploaded_data", ""),
            ):
                full += chunk
                if on_event:
                    await on_event({"type": "token", "content": chunk})
        except BudgetExceeded as e:
            # 综合报告是"最后一步"，额度耗尽时前面查到的数据不能丢：
            # 已经流式产出过半就保留，一个字都没有就用子任务结果拼一份兜底
            full = f"{full}\n\n{_budget_note(e)}" if full else _fallback_report(state)
        state["report"] = full
        if on_event:
            await on_event({"type": "report", "report": full})
        return state

    # ============ 内容生成链路（文案/标题，不拆子任务） ============
    # 注意：角色知识（人格/边界/上下文怎么拼）已收进 ContentAgent（content_gen/content_agent.py），
    # 这里只做调度：跑角色 → 结果放 state → 推 report 事件
    async def content_node(state):
        try:
            state["report"] = await content_agent.run(
                goal=state["goal"],
                history=state.get("history", ""),
                user_profile=state.get("user_profile", ""),
            )
        except BudgetExceeded as e:
            state["report"] = _budget_note(e)
        if on_event:
            await on_event({"type": "report", "report": state["report"]})
        return state

    # ============ 客服问答链路（查知识库） ============
    async def service_node(state):
        try:
            state["report"] = await service_agent.run(
                goal=state["goal"],
                history=state.get("history", ""),
                user_profile=state.get("user_profile", ""),
            )
        except BudgetExceeded as e:
            state["report"] = _budget_note(e)
        if on_event:
            await on_event({"type": "report", "report": state["report"]})
        return state

    # ============ 文档处理链路（解析/优化/生成落盘） ============
    async def document_node(state):
        # 比其它角色多传 uploaded_data：文档原料来自用户上传（state 里已解析好的文本）
        try:
            state["report"] = await document_agent.run(
                goal=state["goal"],
                history=state.get("history", ""),
                user_profile=state.get("user_profile", ""),
                uploaded_data=state.get("uploaded_data", ""),
            )
        except BudgetExceeded as e:
            state["report"] = _budget_note(e)
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
