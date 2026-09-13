"""optimize_document —— 按指令优化/改写文档内容（LLM 工具）"""
from backend.core.llm.base import Message
from backend.core.tool.base import BaseTool, ToolResult, ToolSpec


class OptimizeDocument(BaseTool):
    def __init__(self, llm):
        self.llm = llm

    spec = ToolSpec(
        name="optimize_document",
        description="按指令优化/改写一段文档内容（润色、调语气、精简、规范化排版），返回改写后的全文。",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要优化的文档原文"},
                "instruction": {"type": "string",
                                "description": "优化要求，如'改成正式公文语气''精简到一半''补上小标题'"},
            },
            "required": ["content", "instruction"],
        },
    )

    async def execute(self, content=None, instruction=None, **kwargs):
        # 缺参 / 传错键名由 registry.execute 统一校验并返回可读错误，这里不再做别名兜底
        prompt = (
            "请按下面的要求改写文档，只输出改写后的全文，不要解释、不要加前言后语。\n"
            f"要求：{instruction}\n\n原文：\n{content}"
        )
        response = await self.llm.chat([Message(role="user", content=prompt)])
        return ToolResult(success=True, data={"optimized": response.content})
