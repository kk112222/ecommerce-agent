"""会话管理路由 —— 多会话列表 / 历史 / 重命名 / 删除

前端侧边栏的会话列表就靠这几个接口：
- GET    /api/sessions            当前用户会话列表（最近活跃倒序）
- GET    /api/sessions/{sid}/messages   某会话历史消息（切换时加载）
- PATCH  /api/sessions/{sid}      重命名标题
- DELETE /api/sessions/{sid}      彻底删除（连消息一起物理删除，不可恢复）
"""
from fastapi import APIRouter, Depends

from backend.api.deps import get_current_user
from backend.api.schemas.chat import SessionRenameRequest
from backend.core.memory.store import (
    list_sessions, get_session_messages, rename_session, delete_session,
)
from backend.db.models.user import User

router = APIRouter()


@router.get("/sessions")
async def list_my_sessions(current_user: User = Depends(get_current_user)):
    """当前用户的会话列表（按最近活跃倒序）"""
    return await list_sessions(current_user.id)


@router.get("/sessions/{sid}/messages")
async def history(sid: str, current_user: User = Depends(get_current_user)):
    """某个会话的历史消息（user/assistant），切换会话时前端加载"""
    return await get_session_messages(sid, current_user.id)


@router.patch("/sessions/{sid}")
async def rename(sid: str, body: SessionRenameRequest,
                 current_user: User = Depends(get_current_user)):
    """手动重命名会话标题"""
    await rename_session(sid, current_user.id, body.title)
    return {"ok": True}


@router.delete("/sessions/{sid}")
async def remove(sid: str, current_user: User = Depends(get_current_user)):
    """删除会话（彻底删除：会话和消息一起物理删除）"""
    await delete_session(sid, current_user.id)
    return {"ok": True}
