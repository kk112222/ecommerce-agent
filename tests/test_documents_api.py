"""生成文档的下载接口（离线，TestClient + 覆盖鉴权依赖）

P0-3 的后端那一半：Agent 落盘的文件必须能被**文件主人**下载到，
且别人拿到 URL 也下不走（归属校验在 doc_output.resolve_user_file 里，
这里从 HTTP 层再钉一遍：200 / 404 的行为）。
"""
import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.deps import get_current_user
from backend.db.models.user import User
from backend.infrastructure import doc_output
from backend.infrastructure.doc_output import write_document


@pytest.fixture(autouse=True)
def _isolate_outputs(tmp_path, monkeypatch):
    """换掉 OUTPUTS_DIR，测试不碰真实 outputs/"""
    monkeypatch.setattr(doc_output, "OUTPUTS_DIR", tmp_path)
    yield tmp_path


@pytest.fixture
def client():
    """覆盖鉴权依赖：不用真 JWT/数据库，固定登录成 user 1"""
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="tester")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_download_own_document(client):
    write_document("退货政策要点", "退货要点", "md", user_id=1, session_id="s1")
    res = client.get("/api/documents/s1/退货要点.md")
    assert res.status_code == 200
    assert "退货政策要点" in res.content.decode("utf-8")


def test_download_others_document_404(client):
    """文件属于 user 9，当前登录是 user 1 → 404（不暴露"文件是否存在"）"""
    write_document("机密内容", "报告", "md", user_id=9, session_id="s1")
    res = client.get("/api/documents/s1/报告.md")
    assert res.status_code == 404


def test_download_traversal_404(client):
    res = client.get("/api/documents/s1/..%2F..%2F.env")
    assert res.status_code == 404


def test_list_only_own_documents(client):
    write_document("a", "我的", "md", user_id=1, session_id="s1")
    write_document("b", "别人的", "md", user_id=2, session_id="s1")
    res = client.get("/api/documents")
    assert res.status_code == 200
    names = [d["filename"] for d in res.json()["documents"]]
    assert names == ["我的.md"]
