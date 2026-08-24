from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import jwt
from backend.db.session import get_db
from backend.db.models.user import User
from backend.core.security import verify_token
security = HTTPBearer()
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
        db: AsyncSession = Depends(get_db),
)  -> User:
    """从 token 解析出 user_id → 查库 → 返回 User 对象"""
    # 1. 从请求头取 token（HTTPBearer 已帮你取好了）
    token = credentials.credentials
    # 2. 验证 token，取出 user_id（无效会抛 jwt 异常）
    try:
        user_id = verify_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="无效的token或已过期")
    # 3. 查库拿用户
    user = (
        await db.execute(
            select(User).where(User.id == user_id)
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="用户不存在"
        )
    return user