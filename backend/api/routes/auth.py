from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_db
from backend.db.models.user import User
from backend.core.security import hash_password,verify_password, create_token
from backend.api.schemas.user import RegisterRequest,LoginRequest, TokenResponse
router = APIRouter(prefix="/auth",tags=["auth"])
@router.post("/register",response_model=TokenResponse)
async def register(request: RegisterRequest,db : AsyncSession = Depends(get_db)):
    # 1. 查用户名是否已存在
    exist = (
        await db.execute(
            select(User).where(User.username == request.username)
        )
    ).scalar_one_or_none()
    if exist:
        raise HTTPException(status_code=400,detail="用户已存在")
    # 2. 创建用户，存哈希（bcrypt 加密后的密码）
    user = User(
        username = request.username,
        name = request.name or request.username,
        password_hash = hash_password(request.password),
    )
    db.add(user)
    await db.commit()
    # 3. 注册成功直接返回 token，省得再登一次
    return TokenResponse(
        access_token=create_token(user.id),
        user_id=user.id,
        username=user.username,
    )
@router.post("/login",response_model=TokenResponse)
async def login(request: LoginRequest,db : AsyncSession = Depends(get_db)):
    user = (
        await db.execute(
            select(User).where(User.username == request.username)
        )
    ).scalar_one_or_none()
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401,detail="用户名或密码错误")
    return TokenResponse(
        access_token=create_token(user.id),
        user_id=user.id,
        username=user.username,
    )
