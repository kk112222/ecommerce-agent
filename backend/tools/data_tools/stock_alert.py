from backend.core.tool.base import BaseTool, ToolSpec, ToolResult
from datetime import datetime
from sqlalchemy import select, func
from backend.db.models.product import Product
from backend.db.session import AsyncSessionLocal
class StockAlertTool(BaseTool):
    spec = ToolSpec(
        name="check_stock",
        description="查询库存不足的商品，返回库存低于阈值的商品列表及数量",
        parameters={
            "type":"object",
            "properties":{
                "threshold": {"type": "integer", "description":
  "库存阈值，查找库存低于此值的商品"},   # ✅
            "required":["threshold"]
            }
        }
    )
    async def execute(self,threshold:int) -> ToolResult:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Product.name,Product.category,Product.stock).where(Product.stock<threshold)
            )
            rows = result.all()
            low_stock = [
                {"商品名": row[0], "分类": row[1], "库存": row[2]}
                for row in rows
            ]
        return ToolResult(
            success=True,
            data={
                "货物":low_stock
            }
        )