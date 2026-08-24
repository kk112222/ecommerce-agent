"""数据看板 API —— 返回结构化统计数据，供前端卡片/图表使用"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from backend.api.deps import get_current_user
from sqlalchemy import select, func

from backend.db.session import AsyncSessionLocal
from backend.db.models.order import Order
from backend.db.models.product import Product
from backend.db.models.user import User

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard(
        current_user: User = Depends(get_current_user),
):
    """返回首页看板数据"""
    today = datetime.now()
    today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    async with AsyncSessionLocal() as db:
        # 1. 今日销售额
        r = await db.execute(
            select(func.sum(Order.amount)).where(Order.order_date >= today_start)
        )
        today_sales = r.scalar() or 0

        # 2. 今日订单数
        r = await db.execute(
            select(func.count(Order.id)).where(Order.order_date >= today_start)
        )
        today_orders = r.scalar() or 0

        # 3. 近 7 日销售额（按天聚合）
        r = await db.execute(
            select(func.date(Order.order_date), func.sum(Order.amount))
            .where(Order.order_date >= week_start)
            .group_by(func.date(Order.order_date))
            .order_by(func.date(Order.order_date))
        )
        week_trend = [
            {"date": row[0], "amount": round((row[1] or 0) / 100, 2)}
            for row in r.all()
        ]

        # 4. 库存预警（低于 50）
        r = await db.execute(
            select(func.count(Product.id)).where(Product.stock < 50)
        )
        low_stock_count = r.scalar() or 0

        # 5. 会员消费排行
        r = await db.execute(
            select(User.level, func.sum(Order.amount), func.count(func.distinct(User.id)))
            .join(Order, User.id == Order.user_id)
            .group_by(User.level)
        )
        member_stats = {}
        for row in r.all():
            total = round((row[1] or 0) / 100, 2)
            count = row[2]
            member_stats[row[0]] = {
                "总消费(元)": total,
                "人均消费(元)": round(total / count, 2) if count > 0 else 0,
            }

        # 6. 分类销售占比
        r = await db.execute(
            select(Product.category, func.sum(Order.amount))
            .join(Order, Product.id == Order.product_id)
            .group_by(Product.category)
        )
        category_stats = {
            (row[0] or "未知"): round((row[1] or 0) / 100, 2) for row in r.all()
        }

    return {
        "今日销售额(元)": round(today_sales / 100, 2),
        "今日订单数": today_orders,
        "近7日趋势": week_trend,
        "库存预警数": low_stock_count,
        "会员消费": member_stats,
        "分类销售": category_stats,
    }
