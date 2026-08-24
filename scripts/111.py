import asyncio
from backend.core.security import (hash_password,
                                   verify_password, create_token, verify_token)

# 1. 密码哈希
h = hash_password('mypass123')
print('哈希:', h)
print('验证正确:', verify_password('mypass123', h))
print('验证错误:', verify_password('wrong', h))

# 2. 令牌签发 + 验证
token = create_token(5)
print('token:', token)
print('取出user_id:', verify_token(token))