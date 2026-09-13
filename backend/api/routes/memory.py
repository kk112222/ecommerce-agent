"""长期记忆管理接口 —— 记忆可解释（外部评审 M6）

之前长期记忆"只写不读给用户"：不知道系统记了你什么、也删不掉（delete_session 注释自己都承认
画像跨会话删不到）。加两个接口让用户能看/能删，也是记忆系统可解释性的证明。
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import get_current_user
from backend.core.memory.store import delete_long_term_memory, list_long_term_memories
from backend.db.models.user import User

router = APIRouter()


@router.get("/memory")
async def list_memories(current_user: User = Depends(get_current_user)):
    """列出当前用户生效中的长期记忆（语义画像 + 情景记忆，按时间倒序）

    顺带带上提炼计数：记忆是后台任务，失败了用户只会觉得"它时好时坏"，
    把 ok/skipped/failed/retried 暴露出来，至少能看见它到底跑没跑（P2-16）。
    """
    from backend.api.routes.chat import _EXTRACT_STATS
    return {"memories": await list_long_term_memories(current_user.id),
            "extract_stats": dict(_EXTRACT_STATS)}


@router.delete("/memory/{mem_id}")
async def delete_memory(mem_id: int, current_user: User = Depends(get_current_user)):
    """删除单条长期记忆（连带删它的向量点、重算兜底画像）"""
    ok = await delete_long_term_memory(current_user.id, mem_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}
