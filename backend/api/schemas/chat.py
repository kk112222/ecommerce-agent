from pydantic import BaseModel
class ChatRequest(BaseModel):
    message: str    # 用户发来的消息
    session_id: str =""

class ChatResponse(BaseModel):
    reply: str  # LLM 的回复
    session_id: str
    # 本轮 LLM 用量汇总（P2-11）：调用次数 / tokens / 耗时 / 是否触碰预算上限
    usage: dict | None = None

class SessionRenameRequest(BaseModel):
    title: str  # 会话新标题（手动重命名）


