"""内容创作 Agent —— 文案/标题生成链路的角色封装

【职责边界】
- 本文件只属于"内容创作角色"：人格 prompt + 上下文组装
- 执行复用通用 ReActAgent（backend/agents/data_analysis/simple_agent.py）
- 调度归 supervisor —— 它决定"什么时候派给这个角色"，本文件不知道图的存在

为什么把 prompt 从 supervisor.py 搬出来（兑现踩坑总结里"提示词跟随角色"）：
- supervisor 只该知道"走哪条链路"，不该知道内容创作怎么写
- 以后改文案规则 = 只改这一个文件，不碰图结构
"""
from backend.core.llm.base import Message
from backend.agents.data_analysis.simple_agent import ReActAgent


# 角色的"人格"：你是谁 + 能干什么 + 边界 + 质量要求
# 放模块级常量而不是藏在函数里：改 prompt 不用翻 class，测试也能直接 import
SYSTEM_PROMPT = """你是电商内容创作专员。你只负责根据用户要求生成商品文案/标题，不要分析经营数据。
可用工具：
- title_optimizer：优化/生成商品标题
- copy_generator：生成商品文案/营销话术
只调用内容类工具，不要查销售库存数据。直接产出最终文案。"""


class ContentAgent:
    """内容创作 Agent：固定人格 + 可选对话背景，跑一个独立的 ReAct 循环"""

    def __init__(self, llm, registry):
        self.llm = llm
        # 角色级边界：只装 content 范围的工具。别的工具（查数据、写文件）既不进 schema
        # （模型看不到说明书），也调不动 —— 与 executor 的 analysis 边界对称：
        # 边界靠代码白名单焊死，不靠 SYSTEM_PROMPT 里那句"只调用内容类工具"
        self.registry = registry.subset_scopes(["content"])

    async def run(self, goal: str, history: str = "", user_profile: str = "") -> str:
        """执行一次内容创作，返回最终文案字符串"""
        # 组装上下文（顺序有讲究）：人格 → 历史 → 画像 → 本次目标
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

        # ReAct 循环：LLM 自主决定要不要调 title_optimizer / copy_generator
        agent = ReActAgent(self.llm, self.registry)
        return await agent.run(messages)
