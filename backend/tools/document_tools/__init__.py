"""文档处理工具注册器 —— 解析/优化/生成落盘（新文档 Agent 链路）

context 里带当前 user_id / session_id，用于把生成物隔离到各自的 outputs 子目录。
"""
from backend.core.tool.registry import ToolRegistry
from .optimize_document import OptimizeDocument
from .write_document import WriteDocument


def register_document_tools(registry: ToolRegistry, llm, context: dict | None = None) -> None:
    """注册文档工具；context 可选（scripts 里不传就走 outputs 根目录）"""
    ctx = context or {}
    registry.register(OptimizeDocument(llm))
    registry.register(WriteDocument(user_id=ctx.get("user_id"), session_id=ctx.get("session_id")))
