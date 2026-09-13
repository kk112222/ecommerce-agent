"""文档生成落盘 —— 固定写到项目 outputs/ 目录，带路径安全校验

为什么要单独一层（而不是直接在工具里 open/write）：
- 落盘是「有副作用的 IO」，和安全边界收在一处，工具只管调用；
- 文件名/路径是 LLM 给的（**不可信输入**）→ 必须防目录穿越（../../ 逃出 outputs），
  否则等于把服务器文件系统的写权限交给模型。这是本层存在的核心理由。

目录约定：outputs/{user_<id>}/{session_id}/<filename>.<fmt>
（按用户/会话隔离，避免不同人生成物互相覆盖）
"""
import re
from pathlib import Path

# 项目根 = 本文件上溯三级（backend/infrastructure/doc_output.py → ecommerce-agent/）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = (PROJECT_ROOT / "outputs").resolve()

ALLOWED_FORMATS = {"md", "txt", "docx"}


def _safe_stem(name: str) -> str:
    """把 LLM 给的名字清洗成安全文件名主干（不含扩展名）

    - 只取最后一段（Path(...).name）→ 天然去掉 "a/../../b" 这类目录成分
    - 替换 Windows 非法字符、去掉首尾空格和点（防 ".."、隐藏名）
    - 空名兜底 untitled
    """
    raw = (name or "").strip()
    stem = Path(raw).name                       # 关键：只保留文件名，丢弃任何路径部分
    stem = re.sub(r'^[A-Za-z]:', '', stem)      # Windows 下 "x:报告" 会残留盘符，剥掉
    if "." in stem:                             # 兼容用户给 "报告.md" → 取 报告
        stem = stem.rsplit(".", 1)[0]
    for ch in '<>:"/\\|?*':
        stem = stem.replace(ch, "_")
    stem = stem.strip(" .")
    return stem or "untitled"


def safe_output_path(filename: str, fmt: str,
                     user_id: int | None = None, session_id: str | None = None) -> Path:
    """解析成 outputs 下的安全绝对路径；越界或格式非法直接抛 ValueError"""
    if fmt not in ALLOWED_FORMATS:
        raise ValueError(f"不支持的格式：{fmt}（仅 {'/'.join(sorted(ALLOWED_FORMATS))}）")

    sub = []
    if user_id is not None:
        sub.append(f"user_{int(user_id)}")
    if session_id:
        sub.append(_safe_stem(session_id))
    target_dir = OUTPUTS_DIR.joinpath(*sub) if sub else OUTPUTS_DIR

    target = (target_dir / f"{_safe_stem(filename)}.{fmt}").resolve()
    # 双保险：解析后必须仍在 OUTPUTS_DIR 内（防符号链接/拼接越界）
    if not target.is_relative_to(OUTPUTS_DIR):
        raise ValueError("路径越界，只允许写到 outputs/ 目录内")
    return target


def write_document(content: str, filename: str, fmt: str = "md",
                   user_id: int | None = None, session_id: str | None = None) -> dict:
    """写文档到 outputs，返回 {"path": 相对路径, "bytes": 大小}"""
    path = safe_output_path(filename, fmt, user_id, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "docx":
        from docx import Document                 # 局部 import：md/txt 路径不必依赖 python-docx
        doc = Document()
        for line in (content or "").splitlines():
            if line.startswith("## "):
                doc.add_heading(line[3:].strip(), level=2)
            elif line.startswith("# "):
                doc.add_heading(line[2:].strip(), level=1)
            else:
                doc.add_paragraph(line)
        doc.save(str(path))
    else:
        path.write_text(content or "", encoding="utf-8")

    rel = path.relative_to(OUTPUTS_DIR)
    return {"path": (Path("outputs") / rel).as_posix(), "bytes": path.stat().st_size}


# ==================== 读侧（下载 / 列表） ====================
# 与写侧同样是不信任输入：URL 里的 session_id / filename 由用户构造，必须走同一套清洗 + 越界校验，
# 且永远拼在 user_<当前登录用户> 目录下 —— 归属校验靠"路径里写死 user_id"，不靠事后比对。

def user_root(user_id: int) -> Path:
    """当前用户的产出根目录（下载/列表的唯一入口）"""
    return (OUTPUTS_DIR / f"user_{int(user_id)}").resolve()


def resolve_user_file(user_id: int, session_id: str, filename: str) -> Path:
    """把 URL 参数解析成 outputs 下属于该用户的真实文件；非法或不存在直接抛 ValueError"""
    ext = Path(filename).suffix.lstrip(".").lower()
    if ext not in ALLOWED_FORMATS:
        raise ValueError(f"不支持的格式：{ext or '(无扩展名)'}")
    path = safe_output_path(Path(filename).stem, ext, user_id=user_id, session_id=session_id)
    if not path.is_relative_to(user_root(user_id)):
        raise ValueError("路径越界，只允许访问自己的 outputs 目录")
    if not path.is_file():
        raise ValueError("文件不存在或已被清理")
    return path


def list_documents(user_id: int) -> list[dict]:
    """列出该用户生成过的所有文档（按修改时间倒序），供前端展示下载入口"""
    root = user_root(user_id)
    if not root.is_dir():
        return []
    items = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lstrip(".").lower() in ALLOWED_FORMATS:
            stat = p.stat()
            items.append({
                "session_id": p.parent.name,
                "filename": p.name,
                "bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
                "path": (Path("outputs") / p.relative_to(OUTPUTS_DIR)).as_posix(),
            })
    items.sort(key=lambda x: x["modified_at"], reverse=True)
    return items
