from backend.core.tool.base import BaseTool, ToolSpec, ToolResult
from datetime import datetime
from sqlalchemy import select, func
from backend.db.models.product import Product
from backend.db.session import AsyncSessionLocal
from backend.db.models.order import Order
class CategoryQueryTool(BaseTool):
    spec = ToolSpec(
        name="category_query",
        description="按商品分类查询销售数据（如数码、服饰、家居），可指定时间段。返回该分类的总销售额。",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description":
                    "商品分类，如数码、服饰、家居"},
                "start_date": {"type": "string", "description":
                    "开始日期，可选"},
                "end_date": {"type": "string", "description":
                    "结束日期，可选"},
            },
            "required": ["category",]
        })

    async def execute(self, category: str, start_date: str = None,
                      end_date: str = None):
        async with AsyncSessionLocal() as db:
            conditions = [Product.category==category]
            if start_date:
                start = datetime.strptime(start_date, "%Y-%m-%d")
                conditions.append(Order.order_date >= start)

            if end_date:
                end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23,
                                                                      minute=59, second=59)
                conditions.append(Order.order_date <= end)

            # 查"数码"分类的订单总金额
            result = await db.execute(
                select(func.sum(Order.amount))
                .join(Product, Order.product_id == Product.id)
                .where(*conditions)
            )
            total_amount = result.scalar() or 0
        return ToolResult(
            success=True,
            data={
                "种类": category,
                "时间段": f"{start_date}~~{end_date}",
                "总销售额(元)": round(total_amount / 100, 2),
            }
        )

