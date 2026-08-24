"""营销文案生成工具"""
from backend.core.llm.base import Message
from backend.core.tool.base import BaseTool, ToolSpec, ToolResult


class CopyGenerator(BaseTool):
    spec = ToolSpec(
        name="copy_generator",
        description="为商品生成营销文案，适配不同平台和风格。返回完整推广文案。",
        parameters={
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "商品名称"},
                "selling_points": {"type": "string", "description": "商品卖点，逗号分隔"},
                "style": {"type": "string", "description": "文案风格：专业/活泼/简约/高端"},
                "platform": {"type": "string", "description": "发布平台：淘宝/抖音/小红书"},
                "word_count": {"type": "integer", "description": "大致字数，默认 150"},
            },
            "required": ["product_name", "selling_points", "platform"],
        },
    )

    def __init__(self, llm):
        self.llm = llm

    async def execute(
        self,
        product_name: str,
        selling_points: str,
        platform: str,
        style: str = "活泼",
        word_count: int = 150,
    ):
        prompt = (
            f"你是电商文案专家。请为以下商品撰写一篇{platform}平台推广文案。\n"
            f"- 商品名：{product_name}\n"
            f"- 卖点：{selling_points}\n"
            f"- 风格：{style}\n"
            f"- 字数：约{word_count}字\n"
            f"- 要求：包含emoji，分段清晰，有购买号召"
        )
        response = await self.llm.chat([Message(role="user", content=prompt)])
        return ToolResult(
            success=True,
            data={"商品": product_name, "平台": platform, "风格": style, "文案": response.content},
        )
