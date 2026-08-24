from pydantic import BaseModel
class RegisterRequest(BaseModel):
    username: str
    password: str
    name: str = ""

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
