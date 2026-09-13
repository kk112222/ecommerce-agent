"""文档处理工具注册器 —— 解析/优化/生成落盘（新文档 Agent 链路）

context 里带当前 user_id / session_id，用于把生成物隔离到各自的 outputs 子目录；
还可带 on_document 回调（SSE 场景）——落盘成功后立刻把下载信息推给前端，
不然用户只能读报告里那行文字，点不开文件。
"""
from backend.core.tool.registry import ToolRegistry
from .optimize_document import OptimizeDocument
from .write_document import WriteDocument


def register_document_tools(registry: ToolRegistry, llm, context: dict | None = None) -> None:
    """注册文档工具；context 可选（scripts 里不传就走 outputs 根目录）"""
    ctx = context or {}
    registry.register(OptimizeDocument(llm))
    registry.register(WriteDocument(
        user_id=ctx.get("user_id"),
        session_id=ctx.get("session_id"),
        on_document=ctx.get("on_document"),
    ))
