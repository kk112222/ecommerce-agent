
from datetime import datetime
from backend.core.llm.base import Message
from backend.agents.data_analysis.simple_agent import ReActAgent

class Executor:
    def __init__(self,llm,registry):
        self.llm = llm
        self.registry = registry
    async def run(self, task: dict, uploaded_data: str = "", user_profile: str = "") -> str:
        """跑一个子任务。

        - uploaded_data / user_profile：以前没有 → "结合上传的竞品数据分析"这类子任务
          手里根本没有竞品数据（文档链路能拿到，分析链路拿不到，是断链）
        - tool_hint：以前是死字段（只用了 task['task']）→ 现在据此把注册中心收窄成子集，
          4 个并行子任务不再各自揣着全部工具、靠 prompt 软约束互相越界
        """
        # ① 构造"子任务专属"system prompt
        today = datetime.now().strftime("%Y-%m-%d")  # 算今天
        hint = (task.get("tool_hint") or "").strip()
        allowed = hint if hint in self.registry.tools else ""     # hint 无效/为空 → 放开全集
        system_prompt = f"""你是数据分析子任务执行者,今天是{today}。你被分配了一个明确的子任务，只完成它，不要跑题。

        【你的子任务】
        {task['task']}

        【工作方式】
        1. 先判断要查哪些数据，调用对应工具收集
        2. 如果一次数据不够下结论，继续调用工具补充，直到数据齐全
        3. 数据收集完，给出简明结论

        【输出要求】
        - 用 200 字以内总结：查到了什么 + 发现了什么问题
        - 直接给结论和发现，不要复述调用过程"""
        if allowed:
            system_prompt += f"\n\n【指定工具】本子任务请使用 {allowed}，不要调用其它工具。"
        if user_profile:
            system_prompt += f"\n\n【用户画像】\n{user_profile}"
        if uploaded_data:
            system_prompt += f"\n\n【上传数据】\n{uploaded_data}"
        # ② 复用 ReActAgent 跑一个独立 ReAct 循环（有 hint 时只给它那一个工具）
        registry = self.registry.subset([allowed]) if allowed else self.registry
        agent = ReActAgent(self.llm, registry)
        result = await agent.run([Message(role="system",content=system_prompt)])
        # ③ 空结果兜底：LLM 偶发返回空串，不能让空结果流到综合报告
        if not result or not result.strip():
            return "该子任务未获取到数据：工具查询无结果或执行器未返回内容。"
        return result