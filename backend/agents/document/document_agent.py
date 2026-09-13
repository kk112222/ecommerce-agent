"""文档处理 Agent —— 解析/优化/生成/转换文档的角色封装

【职责边界】与 content_gen / customer_service 完全对称：
- 人格 prompt + 上下文组装（含上传文档）收在本文件
- 执行复用通用 ReActAgent（backend/agents/data_analysis/simple_agent.py）
- 调度归 supervisor —— 它决定何时把问题派给文档角色

【为什么要把上传文档单独喂进来】
用户说"优化我刚传的文档"时，文档正文由 chat.py 解析后放进 state["uploaded_data"]，
本 Agent 把它作为独立 system 块注入，LLM 才有原料可用（否则只会瞎编）。
"""
from backend.core.llm.base import Message
from backend.agents.data_analysis.simple_agent import ReActAgent


# 角色人格：你是谁 + 能干什么 + 工具 + 边界（放模块级常量，改 prompt 不翻类）
SYSTEM_PROMPT = """你是电商团队的文档专员，负责处理运营类文档。你能做四件事：
1. 解析并结构化文档：把用户上传/粘贴的文档整理成要点、大纲或表格；
2. 优化改写：按用户要求润色、调整语气、精简、规范排版；
3. 生成新文档：把成果写成一个文件保存下来；
4. 格式转换：把内容按 md / txt / docx 重新保存。

可用工具：
- optimize_document：按指令改写一段文档内容（需要 content 和 instruction）
- write_document：把文档内容保存成文件（需要 filename、content，可选 format）

规则：
- 用户上传的文档内容在【上传文档】块里，直接用它作为原料；若用户让你处理文档但【上传文档】块为空，如实说"没看到上传的文档，请先上传"，不要编造内容。
- 生成/保存文档时必须调用 write_document，filename 只给文件名（不要给路径），系统会存到固定目录；保存成功后把工具返回的 path 告诉用户。
- 只做文档相关的事，不要查销售/库存/会员数据（那属于数据分析，不是你的职责）。"""


class DocumentAgent:
    """文档处理 Agent：解析 → 优化 → 落盘，跑一个独立的 ReAct 循环"""

    def __init__(self, llm, registry):
        self.llm = llm
        self.registry = registry

    async def run(self, goal: str, history: str = "", user_profile: str = "",
                  uploaded_data: str = "") -> str:
        """执行一次文档任务，返回给用户的说明文本"""
        # 上下文顺序：人格 → 历史 → 画像 → 上传文档 → 本次目标
        messages = [Message(role="system", content=SYSTEM_PROMPT)]
        if history:
            messages.append(Message(role="system", content=f"用户之前的对话：\n{history}"))
        if user_profile:
            messages.append(Message(role="system", content=f"用户画像：\n{user_profile}"))
        if uploaded_data:
            messages.append(Message(role="system", content=f"【上传文档】\n{uploaded_data}"))
        messages.append(Message(role="user", content=goal))

        # ReAct 循环：LLM 自主决定调 optimize_document / write_document 几次
        agent = ReActAgent(self.llm, self.registry)
        return await agent.run(messages)
