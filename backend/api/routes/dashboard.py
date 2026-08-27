"""数据看板 API —— 返回结构化统计数据，供前端卡片/图表使用"""
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from backend.api.deps import get_current_user
from sqlalchemy import select, func

from backend.db.session import AsyncSessionLocal
from backend.db.models.order import Order
from backend.db.models.product import Product
from backend.db.models.user import User
from backend.core.llm.factory import create_llm
from backend.core.llm.base import Message

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


# ==================== AI 经营洞察 ====================

def _pct(cur: int, prev: int) -> str:
    """环比/同比字符串：'+12%' / '-5%' / '持平' / '新增' / '无对比基数'（单位分）"""
    if prev == 0:
        return "新增" if cur > 0 else "无对比基数"
    d = (cur - prev) / prev * 100
    if abs(d) < 0.5:
        return "持平"
    return f"{'+' if d > 0 else ''}{d:.0f}%"


def _build_insight_prompt(data: dict) -> str:
    """经营数据快照 → LLM 的 system prompt（洞察质量全靠数据喂得全，对比才有判断）"""
    cat_lines = "\n".join(
        f"  - {c}：¥{y}（较上周同日 {_pct(t, w)}）"
        for c, (y, t, w) in data["分类环比"].items()
    ) or "  - 今日无分类销售数据"
    stock_lines = "\n".join(
        f"  - {name} 剩 {stock} 件" for name, stock in data["库存预警"]
    ) or "  - 无库存预警"
    trend_lines = "\n".join(f"  - {d}：¥{v}" for d, v in data["近7日趋势"]) or "  - 无数据"
    top_lines = "\n".join(
        f"  - {i + 1}. {name} ¥{v}" for i, (name, v) in enumerate(data["Top热销"])
    ) or "  - 无数据"

    return f"""你是电商运营分析师。下面是当前店铺的经营数据快照，请输出一份简短的经营洞察报告。

【经营数据快照】
- 今日销售额：¥{data["今日销售额"]}（较昨日 {data["今日环比"]}，较上周同日 {data["今日同比"]}）
- 今日订单数：{data["今日订单"]} 单（较昨日 {data["订单环比"]}）
- 各分类销售额（今日 vs 上周同日）：
{cat_lines}
- 库存预警（低于 50 件）：
{stock_lines}
- 近 7 日每日销售额：
{trend_lines}
- 近 7 日 Top3 热销商品：
{top_lines}

【输出要求】
1. 输出 3~5 条要点，每条 = 观察 + 数字依据 + 一句行动建议
2. 优先指出：异常涨跌、库存风险、机会点（热销分类/商品）
3. 如果今日数据为 0，明确指出"今日暂无数据"，建议核实同步，不要强行解读
4. 用 markdown 无序列表（每项一行），语言像给运营的晨报，简洁直接"""


@router.get("/dashboard/insight")
async def get_dashboard_insight(
        current_user: User = Depends(get_current_user),
):
    """AI 经营洞察：把看板数据快照（含环比/明细/Top）喂给 LLM，返回自然语言经营解读"""
    start = time.time()
    today = datetime.now()
    today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    last_week_start = today_start - timedelta(days=7)   # 上周同日整天的起止
    last_week_end = today_start - timedelta(days=6)
    week_start = today_start - timedelta(days=7)        # 近 7 日（含今日）

    async with AsyncSessionLocal() as db:
        async def _sum_sales(start, end):
            r = await db.execute(select(func.sum(Order.amount)).where(
                Order.order_date >= start, Order.order_date < end))
            return r.scalar() or 0

        async def _count_orders(start, end):
            r = await db.execute(select(func.count(Order.id)).where(
                Order.order_date >= start, Order.order_date < end))
            return r.scalar() or 0

        async def _cat_sales(start, end):
            r = await db.execute(
                select(Product.category, func.sum(Order.amount))
                .join(Order, Product.id == Order.product_id)
                .where(Order.order_date >= start, Order.order_date < end)
                .group_by(Product.category)
            )
            return {c: (v or 0) for c, v in r.all()}

        # 今日 / 昨日 / 上周同日 的销售额与订单
        today_sales = await _sum_sales(today_start, today)
        yest_sales = await _sum_sales(yesterday_start, today_start)
        week_sales = await _sum_sales(last_week_start, last_week_end)
        today_orders = await _count_orders(today_start, today)
        yest_orders = await _count_orders(yesterday_start, today_start)

        # 各分类：今日 vs 上周同日（取两天的分类并集，让 LLM 能看到消失/新增的分类）
        today_cat = await _cat_sales(today_start, today)
        week_cat = await _cat_sales(last_week_start, last_week_end)
        all_cats = set(today_cat) | set(week_cat)
        cat_comp = {c: (f"{today_cat.get(c, 0) / 100:,.2f}", today_cat.get(c, 0), week_cat.get(c, 0)) for c in all_cats}

        # 库存预警明细（低于 50，按库存升序排）
        r = await db.execute(
            select(Product.name, Product.stock).where(Product.stock < 50).order_by(Product.stock)
        )
        low_stock = [(name, stock) for name, stock in r.all()]

        # 近 7 日趋势 + Top3 热销商品
        r = await db.execute(
            select(func.date(Order.order_date), func.sum(Order.amount))
            .where(Order.order_date >= week_start)
            .group_by(func.date(Order.order_date))
            .order_by(func.date(Order.order_date))
        )
        trend = [(d, f"{v / 100:,.0f}") for d, v in r.all()]

        r = await db.execute(
            select(Product.name, func.sum(Order.amount))
            .join(Order, Product.id == Order.product_id)
            .where(Order.order_date >= week_start)
            .group_by(Product.id)
            .order_by(func.sum(Order.amount).desc())
            .limit(3)
        )
        top = [(name, f"{v / 100:,.0f}") for name, v in r.all()]

    data = {
        "今日销售额": f"{today_sales / 100:,.2f}",
        "今日环比": _pct(today_sales, yest_sales),
        "今日同比": _pct(today_sales, week_sales),
        "今日订单": today_orders,
        "订单环比": _pct(today_orders, yest_orders),
        "分类环比": cat_comp,
        "库存预警": low_stock,
        "近7日趋势": trend,
        "Top热销": top,
    }

    llm = create_llm()
    try:
        response = await llm.chat([Message(role="system", content=_build_insight_prompt(data))],
                                  temperature=0.3)
        # 千问有时把换行输出成字面 \n，这里转成真实换行，否则 markdown 列表会糊成一行
        insight = response.content.strip().replace("\\n", "\n")
        error = None
    except Exception as e:   # LLM 挂了返回空洞察 + 错误信息，前端兜底提示，不让看板崩
        insight, error = "", str(e)

    return {
        "insight": insight,
        "cost_ms": round((time.time() - start) * 1000),
        "error": error,
    }
