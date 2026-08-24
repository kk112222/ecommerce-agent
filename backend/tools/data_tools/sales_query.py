from backend.core.tool.base import BaseTool, ToolSpec, ToolResult
from datetime import datetime
from sqlalchemy import select, func
from backend.db.session import AsyncSessionLocal
from backend.db.models.order import Order
class SalesQueryTool(BaseTool):
    spec = ToolSpec(
        name="query_sales",
        description="查询某段时间的销售数据，返回总销售额和订单数",
        parameters={
            "type": "object",
            "properties": {
                "start_date": {"type": "string","description":"开始日期"},
                "end_date": {"type": "string", "description": "结束日期"},
            },
            "required": ["start_date", "end_date"],
        }
    )
    async def execute(self,start_date:str,end_date:str):
        # 干活：真正执行查询
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23,
                                                              minute=59, second=59)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(func.sum(Order.amount))
                .where(Order.order_date >= start, Order.order_date <= end)
            )
            total_amount = result.scalar() or 0
            result = await db.execute(
                select(func.count(Order.id))
                .where(Order.order_date >= start, Order.order_date <= end)
            )
            total_orders = result.scalar() or 0
        return ToolResult(
            success=True,
            data={
                "时间段": f"{start_date}~~{end_date}",
                "总销售额(元)": round(total_amount / 100, 2),
                "总订单数": total_orders,
            }
        )

