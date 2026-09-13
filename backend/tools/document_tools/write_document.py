"""write_document —— 把文档写进 outputs/ 目录（落盘工具）

user_id / session_id 在构造时注入（来自当前登录用户和会话），
LLM 只能给 filename/content/format，**不能指定任意路径** —— 路径安全收在 doc_output 层。
"""
from backend.core.tool.base import BaseTool, ToolResult, ToolSpec
from backend.infrastructure.doc_output import write_document


class WriteDocument(BaseTool):
    def __init__(self, user_id: int | None = None, session_id: str | None = None):
        self.user_id = user_id
        self.session_id = session_id

    spec = ToolSpec(
        name="write_document",
        description="把文档内容保存成文件到系统 outputs 目录（支持 md/txt/docx）。用于生成新文档，或把内容转成别的格式保存。",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string",
                             "description": "文件名，不含路径和扩展名，如 竞品分析报告"},
                "content": {"type": "string", "description": "文档全文"},
                "format": {"type": "string",
                           "description": "文件格式：md / txt / docx，默认 md"},
            },
            "required": ["filename", "content"],
        },
    )

    async def execute(self, filename=None, content=None, format="md", **kwargs):
        # 缺参 / 传错键名由 registry.execute 统一校验并返回可读错误，这里不再做别名兜底
        fmt = str(format or "md").lower().lstrip(".")   # 值规范化：容忍 LLM 传 ".docx" / "Markdown"
        try:
            info = write_document(
                content, filename, fmt,
                user_id=self.user_id, session_id=self.session_id,
            )
        except ValueError as e:                    # 格式非法 / 路径越界 → 交给 LLM 如实说明
            return ToolResult(success=False, data=None, error=str(e))
        return ToolResult(success=True, data=info)
