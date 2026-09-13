"""write_document —— 把文档写进 outputs/ 目录（落盘工具）

user_id / session_id 在构造时注入（来自当前登录用户和会话），
LLM 只能给 filename/content/format，**不能指定任意路径** —— 路径安全收在 doc_output 层。
"""
import logging
from pathlib import Path

from backend.core.tool.base import BaseTool, ToolResult, ToolSpec
from backend.infrastructure.doc_output import write_document

logger = logging.getLogger(__name__)


class WriteDocument(BaseTool):
    def __init__(self, user_id: int | None = None, session_id: str | None = None,
                 on_document=None):
        self.user_id = user_id
        self.session_id = session_id
        # 落盘成功后的回调（SSE 场景：把下载信息推给前端）。同步 callable，可以不给。
        self.on_document = on_document

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

        if self.on_document and self.user_id is not None and self.session_id:
            # 推给前端的下载信息（前端拼 /api/documents/{sid}/{filename} 即得下载地址）
            try:
                self.on_document({"type": "document", **info,
                                  "filename": Path(info["path"]).name,
                                  "session_id": self.session_id})
            except Exception:                      # 推送失败不能影响落盘结果
                logger.exception("推送 document 事件失败")
        return ToolResult(success=True, data=info)
