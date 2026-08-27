from pydantic import BaseModel
class ChatRequest(BaseModel):
    message: str    # 用户发来的消息
    session_id: str =""

class ChatResponse(BaseModel):
    reply: str  # LLM 的回复
    session_id: str

class SessionRenameRequest(BaseModel):
    title: str  # 会话新标题（手动重命名）


