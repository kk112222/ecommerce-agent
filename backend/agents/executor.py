
from datetime import datetime
from backend.core.llm.base import Message
from backend.agents.data_analysis.simple_agent import ReActAgent

class Executor:
    def __init__(self,llm,registry):
        self.llm = llm
        self.registry = registry
    async def run(self,task:dict) -> str:
        # ① 构造"子任务专属"system prompt
        today = datetime.now().strftime("%Y-%m-%d")  # 算今天
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
        # ② 复用 ReActAgent 跑一个独立 ReAct 循环
        agent = ReActAgent(self.llm,self.registry)
        result = await agent.run([Message(role="system",content=system_prompt)])
        # ③ 空结果兜底：LLM 偶发返回空串，不能让空结果流到综合报告
        if not result or not result.strip():
            return "该子任务未获取到数据：工具查询无结果或执行器未返回内容。"
        return result