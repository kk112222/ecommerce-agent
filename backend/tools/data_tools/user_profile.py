from backend.core.tool.base import BaseTool, ToolSpec, ToolResult
from backend.db.models.order import Order
from backend.db.models.user import User
from backend.db.session import AsyncSessionLocal
from sqlalchemy import select, func

class UserProfileTool(BaseTool):
    spec = ToolSpec(
        name="user_profile",
        description="分析会员消费数据，按等级统计订单数、总消费、人均消费。可选指定等级。",
        parameters={
            "type":"object",
            "properties":{
                "level": {"type":"string","description":"会员等级：vip/svip/normal，不填则查全部"},
            },
            "required": []
        }
    )
    async def execute(self,level:str =None):
        async with AsyncSessionLocal() as db:
            conditions = []
            if level:
                conditions.append(User.level == level)
            result = await db.execute(
                select(
                    User.level,
                    func.count(Order.id),
                    func.sum(Order.amount),
                    func.count(func.distinct(User.id))
                )
                .join(Order,User.id == Order.user_id)
                .where(*conditions)
                .group_by(User.level)
            )
            rows = result.all()
            profile = {}
            for row in rows:
                amount_yuan = round((row[2] or 0) / 100, 2)
                profile[row[0]] = {
                    "总订单数": row[1],
                    "总消费金额(元)": amount_yuan,
                    "人数": row[3],
                    "人均消费(元)": round(amount_yuan / row[3], 2)
                    if row[3] > 0 else 0,
                }
            return ToolResult(
                success=True,
                data=profile
            )