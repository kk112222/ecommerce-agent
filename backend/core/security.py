import jwt
import bcrypt
from datetime import datetime,timedelta,timezone
from backend.core.config import settings
# 密钥放哪？从 settings 读，不写死在代码里（见下面"配置"）
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = settings.jwt_expire_minutes  # 默认 24 小时，.env 可调
def hash_password(password:str) -> str:
    """密码 → bcrypt 哈希（自动加盐）"""
    return bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode()

def verify_password(password:str , hashed:str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id:int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token:str) -> int:
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    return int(payload["sub"])