"""文档落盘的路径安全（离线，写进 tmp_path 不碰真实 outputs/）

这是全项目唯一接受「LLM 可控路径」去写盘的地方，安全断言必须钉死：
目录穿越逃不出去、非法字符被清洗、格式走白名单。
"""
import pytest

from backend.infrastructure import doc_output
from backend.infrastructure.doc_output import safe_output_path, write_document


@pytest.fixture(autouse=True)
def _isolate_outputs(tmp_path, monkeypatch):
    """把 OUTPUTS_DIR 换到临时目录，测试不污染真实 outputs/"""
    monkeypatch.setattr(doc_output, "OUTPUTS_DIR", tmp_path)
    yield


def test_path_traversal_blocked(tmp_path):
    """../../evil 只能落在 outputs 内，文件名被剥成 evil"""
    p = safe_output_path(r"../../evil", "md", user_id=1, session_id="s")
    assert p.is_relative_to(tmp_path)
    assert p.name == "evil.md"


def test_illegal_chars_and_dirs_sanitized(tmp_path):
    """路径成分 + Windows 非法字符 + 盘符 全被清洗"""
    p = safe_output_path(r"a/b/c:报告*.md", "docx", user_id=1)
    assert p.is_relative_to(tmp_path)
    assert p.name.startswith("报告") and p.suffix == ".docx"


def test_format_whitelist():
    with pytest.raises(ValueError):
        safe_output_path("x", "exe", user_id=1)


def test_per_user_session_isolation(tmp_path):
    """按 用户/会话 分子目录，两个用户的同名文件互不覆盖"""
    a = safe_output_path("报告", "md", user_id=1, session_id="s1")
    b = safe_output_path("报告", "md", user_id=2, session_id="s1")
    assert a != b
    assert "user_1" in a.parts and "user_2" in b.parts


@pytest.mark.parametrize("fmt", ["md", "txt", "docx"])
def test_write_document_formats(tmp_path, fmt):
    info = write_document("# 标题\n正文内容", "验证文档", fmt, user_id=7, session_id="s")
    assert info["path"].startswith("outputs/")
    assert info["bytes"] > 0
    assert (tmp_path / "user_7" / "s" / f"验证文档.{fmt}").exists()
