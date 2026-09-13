"""客服问答 Agent —— 知识库问答链路的角色封装

和 content_agent.py 完全对称的结构：
- 人格 prompt 在这里
- 执行复用 ReActAgent
- 调度归 supervisor
"""
from backend.core.llm.base import Message
from backend.agents.data_analysis.simple_agent import ReActAgent


SYSTEM_PROMPT = """你是电商售后客服。用户问的是售后政策、退货规则等问题，需要查知识库才能准确回答。
可用工具：
- search_knowledge_base：检索客服知识库
先检索知识库拿到政策原文，再据此回答。如果知识库里没有答案，如实说"知识库没有查到"，不要编造。"""


class ServiceAgent:
    """客服问答 Agent：先检索知识库、再据原文回答，查不到就直说"""

    def __init__(self, llm, registry):
        self.llm = llm
        self.registry = registry

    async def run(self, goal: str, history: str = "", user_profile: str = "") -> str:
        """执行一次客服问答，返回回答字符串"""
        # 组装上下文（顺序同 ContentAgent）：人格 → 历史 → 画像 → 本次问题
        messages = [Message(role="system", content=SYSTEM_PROMPT)]
        if history:
            messages.append(Message(
                role="system", content=f"用户之前的对话：\n{history}",
            ))
        if user_profile:
            messages.append(Message(
                role="system", content=f"用户画像：\n{user_profile}",
            ))
        messages.append(Message(role="user", content=goal))

        agent = ReActAgent(self.llm, self.registry)
        return await agent.run(messages)
