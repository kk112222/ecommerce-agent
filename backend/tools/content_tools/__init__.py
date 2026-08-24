"""
内容生成工具注册器（Phase 2 实现）
"""
from backend.core.tool.registry import ToolRegistry
from .title_optimizer import TitleOptimizer
from .copy_generator import CopyGenerator


def register_content_tools(registry: ToolRegistry, llm) -> None:
    """注册所有内容生成工具"""
    registry.register(TitleOptimizer(llm))
    registry.register(CopyGenerator(llm))
