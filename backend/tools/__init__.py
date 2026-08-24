"""
工具总注册器 —— 把三个分类的工具全部注册到同一个 ToolRegistry
"""
from backend.core.tool.registry import ToolRegistry

from .data_tools import register_data_tools
from .content_tools import register_content_tools
from .service_tools import register_service_tools


def register_all_tools(registry: ToolRegistry, llm) -> None:
    """一次性注册所有工具。content 类工具需要 llm 实例来生成文案。"""
    register_data_tools(registry)
    register_content_tools(registry, llm)
    register_service_tools(registry)
