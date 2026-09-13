"""生成文档的下载 / 列表接口（P0-3：文档链路闭环）

修复前：Agent 回复"已保存到 outputs/xxx.md"，但网页上既看不到也点不开 ——
用户只能去服务器磁盘翻文件，功能等于半截。

归属校验的做法：URL 里的 session_id / filename 是**用户可控输入**，
不拿它们直接拼路径，而是统一交给 doc_output.resolve_user_file：
清洗文件名 → 拼到 outputs/user_<当前登录用户>/ 下 → 校验解析结果没越界 → 文件存在。
路径里写死了 user_id，所以天然不可能下载到别人的文件（不是"事后比对 owner 字段"那种易漏的写法）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.api.deps import get_current_user
from backend.db.models.user import User
from backend.infrastructure.doc_output import list_documents, resolve_user_file

logger = logging.getLogger(__name__)

router = APIRouter()

# 扩展名 → Content-Type / 下载文件名（docx 必须是这个 MIME，浏览器才会当文件下载）
_MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@router.get("/documents")
async def list_my_documents(current_user: User = Depends(get_current_user)):
    """列出当前用户生成过的文档（刷新页面后仍能找回下载入口）"""
    return {"documents": list_documents(current_user.id)}


@router.get("/documents/{session_id}/{filename}")
async def download_document(session_id: str, filename: str,
                            current_user: User = Depends(get_current_user)):
    """下载自己生成的文档（路径越界 / 不属于自己 / 不存在 → 404）"""
    try:
        path = resolve_user_file(current_user.id, session_id, filename)
    except ValueError as e:
        # 不区分"越界"和"不存在"，统一 404：不给探测别人文件是否存在的机会
        raise HTTPException(status_code=404, detail=str(e))

    fmt = path.suffix.lstrip(".").lower()
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(fmt, "application/octet-stream"),
        filename=path.name,
    )
