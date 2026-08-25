import json
import re
from backend.core.llm.base import Message

class Planner:
    def __init__(self,llm,registry):
        self.llm = llm
        self.registry = registry

    async def plan(self, goal: str, history: str = "", user_profile: str = "",
                   uploaded_data: str = "") -> list[dict]:
        tool_list = "\n".join(
            f"- {tool.spec.name}: {tool.spec.description}"
            for tool in self.registry.tools.values()
        )
        system_prompt = f"""你是任务规划器，负责把用户的电商运营目标拆解成可并行执行的子任务。

        【可用工具】
        {tool_list}

        【硬规则】
        1. 把目标拆成 2~5 个互相独立的子任务，每个子任务必须能独立完成，不能依赖其他子任务的输出
        2. 每个子任务尽量匹配一个最合适的工具名填在 tool_hint；不确定就留空字符串
        3. 只输出一个 JSON 数组，不要任何解释、不要 markdown 代码块、不要多余文字

        【输出格式】
        [
          {{"id": "t1", "task": "一句话说明要查什么数据", "tool_hint": "工具名"}},
          {{"id": "t2", "task": "一句话说明要查什么数据", "tool_hint": "工具名"}}
        ]"""
        if history:
            system_prompt += f"\n\n【历史对话背景】\n{history}"
        if user_profile:
            system_prompt += f"\n\n【用户画像】\n{user_profile}"
        if uploaded_data:
            system_prompt += f"\n\n【上传数据】\n{uploaded_data}"
        messages = [
            Message(role="system",content=system_prompt),
            Message(role="user",content=goal),
        ]
        response = await self.llm.chat(messages,temperature=0.3)
        text = re.sub(r'^```(?:json)?|```$', '', response.content.strip(), flags=re.M)
        try:
            plan = json.loads(text)
        except json.decoder.JSONDecodeError:
            plan = None
        if not isinstance(plan,list):
            return [{"id":"tl","task":goal,"tool_hint":""}]
        return plan