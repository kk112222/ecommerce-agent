"""文件上传路由 —— 上传 CSV/PDF/MD 等文件，解析成文本后按会话存库，聊天时 Agent 能看到"""
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.api.deps import get_current_user
from backend.core.memory.store import save_uploaded_doc
from backend.db.models.user import User
from backend.infrastructure.vector_store.doc_processor import parse_file

router = APIRouter()

# 允许的格式（对应 doc_processor 支持的解析器）
ALLOWED_SUFFIX = {".csv", ".tsv", ".md", ".pdf", ".docx", ".html", ".htm"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    current_user: User = Depends(get_current_user),
):
    """上传文件 → 解析成文本 → 按会话存库

    前端：FormData 里放 file + session_id，带 Bearer Token。
    聊天时后端会把这个会话上传过的文件内容注入 Agent（见 chat.py）。
    """
    filename = file.filename or "unnamed"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(400, f"暂不支持 {suffix or '无后缀'} 格式，支持: {sorted(ALLOWED_SUFFIX)}")

    raw = await file.read()
    if len(raw) > MAX_SIZE:
        raise HTTPException(400, f"文件超过 {MAX_SIZE // 1024 // 1024}MB 上限")

    # 存临时文件让 parse_file 按后缀选解析器（它靠后缀分派）
    with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        content = parse_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not content.strip():
        raise HTTPException(400, "文件解析后没有内容，请检查文件格式")

    await save_uploaded_doc(session_id, current_user.id, filename, content)
    return {
        "filename": filename,
        "chars": len(content),
        "preview": content[:200],
    }
