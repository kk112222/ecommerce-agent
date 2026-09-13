"""
工具总注册器 —— 把三个分类的工具全部注册到同一个 ToolRegistry
"""
from backend.core.tool.registry import ToolRegistry

from .data_tools import register_data_tools
from .content_tools import register_content_tools
from .service_tools import register_service_tools
from .document_tools import register_document_tools


def register_all_tools(registry: ToolRegistry, llm, context: dict | None = None) -> None:
    """一次性注册所有工具。content 类工具需要 llm 实例来生成文案。

    context（可选）：{"user_id":..., "session_id":...}，文档落盘工具据此把生成物
    隔离到 outputs/ 下各自子目录；scripts 等离线调用不传则落 outputs 根。
    """
    register_data_tools(registry)
    register_content_tools(registry, llm)
    register_service_tools(registry)
    register_document_tools(registry, llm, context)
