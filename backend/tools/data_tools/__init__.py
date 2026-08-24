"""
数据分析工具注册器
以后每加一个工具，只需要：
1. from .新工具 import 新工具类
2. registry.register(新工具类())
"""
from backend.core.tool.registry import ToolRegistry
from .sales_query import SalesQueryTool
from .category_query import CategoryQueryTool
from .stock_alert import StockAlertTool
from .user_profile import UserProfileTool
from .product_query import ProductQueryTool

def register_data_tools(registry: ToolRegistry) -> None:
    """注册所有数据分析工具"""
    registry.register(SalesQueryTool())
    registry.register(CategoryQueryTool())
    registry.register(StockAlertTool())
    registry.register(UserProfileTool())
    registry.register(ProductQueryTool())
