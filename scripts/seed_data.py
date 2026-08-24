import asyncio
from datetime import datetime, timedelta
import random

import bcrypt

from backend.db.session import AsyncSessionLocal
from backend.db.models.product import Product
from backend.db.models.user import User
from backend.db.models.order import Order

# 统一测试密码，登录时用
TEST_PASSWORD = "admin123"


def _hash(pwd: str) -> str:
    return bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def seed():
    async with AsyncSessionLocal() as db:
        products = [
            Product(name="苹果15", category="数码",
                    price=699900, cost=500000, stock=5),
            Product(name="无线耳机", category="数码", price=29900,
                    cost=15000, stock=200),
            Product(name="机械键盘", category="数码", price=39900,
                    cost=20000, stock=12),
            Product(name="运动鞋", category="服饰", price=49900,
                    cost=25000, stock=150),
            Product(name="双肩包", category="服饰", price=14900,
                    cost=8000, stock=120),
            Product(name="保温杯", category="家居", price=9900,
                    cost=5000, stock=300),
            Product(name="台灯", category="家居", price=19900,
                    cost=10000, stock=8),
            Product(name="充电宝", category="数码", price=12900,
                    cost=7000, stock=30),
        ]
        db.add_all(products)
        await db.flush()
        #提交到数据库但不结束事务，才能拿到自动生成的id
        # ===== 2. 插入用户 =====
        users = [
            User(username="zhangsan", name="张三", level="vip", password_hash=_hash(TEST_PASSWORD)),
            User(username="lisi", name="李四", level="normal", password_hash=_hash(TEST_PASSWORD)),
            User(username="wangwu", name="王五", level="svip", password_hash=_hash(TEST_PASSWORD)),
            User(username="zhaoliu", name="赵六", level="normal", password_hash=_hash(TEST_PASSWORD)),
            User(username="qianqi", name="钱七", level="vip", password_hash=_hash(TEST_PASSWORD)),
        ]
        db.add_all(users)
        await db.flush()
        # ===== 3. 插入订单（过去 30 天的随机订单） =====
        today =datetime.now()
        orders = []
        for _ in range(100):
            product = random.choice(products)
            user = random.choice(users)
            days_ago = random.randint(0,30)
            quantity = random.randint(1,5)
            orders.append(Order(
                product_id=product.id,
                user_id=user.id,
                quantity=quantity,
                amount=product.price * quantity,
                order_date=today - timedelta(days=days_ago),
                status=random.choice(["pending", "shipped","completed",
                                      "completed"])
            ))
        db.add_all(orders)
        await db.commit()
        print(f"[OK] 模拟数据插入完成！")
        print(f"     商品: {len(products)} 条")
        print(f"     用户: {len(users)} 条")
        print(f"     订单: {len(orders)} 条")

if __name__ == "__main__":
    asyncio.run(seed())
