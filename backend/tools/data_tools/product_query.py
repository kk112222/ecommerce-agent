"""按商品名称模糊搜索：销售量 + 库存 + 价格"""
from datetime import datetime

from sqlalchemy import select, func

from backend.core.tool.base import BaseTool, ToolSpec, ToolResult
from backend.db.models.product import Product
from backend.db.models.order import Order
from backend.db.session import AsyncSessionLocal


class ProductQueryTool(BaseTool):
    spec = ToolSpec(
        name="product_query",
        description="按商品名称模糊搜索，返回该商品的库存、单价、总销售额。可查询库存量和销售情况。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "商品名称关键词，支持模糊匹配"},
                "start_date": {"type": "string", "description": "开始日期，可选"},
                "end_date": {"type": "string", "description": "结束日期，可选"},
            },
            "required": ["keyword"],
        },
    )

    async def execute(self, keyword: str, start_date: str = None, end_date: str = None):
        async with AsyncSessionLocal() as db:
            # 1. 查匹配的商品信息
            product_result = await db.execute(
                select(Product.id, Product.name, Product.category, Product.price, Product.stock)
                .where(Product.name.contains(keyword))
            )
            products = product_result.all()

            if not products:
                return ToolResult(
                    success=True,
                    data={"商品关键词": keyword, "结果": "未找到匹配商品"},
                )

            # 取第一个匹配的商品
            pid, pname, pcat, pprice, pstock = products[0]

            # 2. 查该商品的销售数据
            sales_conditions = [Order.product_id == pid]
            if start_date:
                start = datetime.strptime(start_date, "%Y-%m-%d")
                sales_conditions.append(Order.order_date >= start)
            if end_date:
                end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                sales_conditions.append(Order.order_date <= end)

            sales_result = await db.execute(
                select(func.sum(Order.amount), func.count(Order.id))
                .where(*sales_conditions)
            )
            total_amount, order_count = sales_result.one()

            # 3. 记录匹配到的所有同名商品（有多个时提示）
            names = list({p[1] for p in products})

        return ToolResult(
            success=True,
            data={
                "商品名": names[0] if len(names) == 1 else f"{names[0]}（还有多个匹配: {names[1:]}）",
                "分类": pcat,
                "单价(元)": round(pprice / 100, 2),
                "库存": pstock,
                "时间段": f"{start_date or '不限'} ~ {end_date or '不限'}",
                "总销售额(元)": round((total_amount or 0) / 100, 2),
                "订单数": order_count or 0,
            },
        )
