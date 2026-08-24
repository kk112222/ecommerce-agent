from backend.core.llm.base import Message
from backend.core.tool.base import BaseTool, ToolResult, ToolSpec


class TitleOptimizer(BaseTool):
    def __init__(self,llm):
        self.llm = llm
    spec = ToolSpec(
        name="title_optimizer",
        description="为商品优化标题，适配不同电商平台。返回多条标题建议。",
        parameters={
            "type":"object",
            "properties": {
                "product_name": {"type": "string",
                                 "description": "商品名称"},
                "keywords": {"type": "string",
                             "description": "SEO关键词，逗号分隔"},
                "platform": {"type": "string",
                             "description": "平台：淘宝/抖音/小红书"},
            },
            "required": ["product_name", "keywords",
                         "platform"],
        }
    )
    async def execute(self,product_name,keywords,platform):
        prompt = f"为'{product_name}'生成3条{platform}平台标题，关键词: {keywords}"
        response = await self.llm.chat([Message(role="user",content=prompt)])
        return ToolResult(success=True,data={
            "标题":response.content,
        })