"""
客服工具注册器（Phase 3）
"""
from backend.core.tool.registry import ToolRegistry
from .rag_search import RAGTool


def register_service_tools(registry: ToolRegistry) -> None:
    """注册所有客服工具"""
    registry.register(RAGTool())
