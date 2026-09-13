"""SSE 主链路（离线：假 LLM 跑真图 + 旁路数据库，不联网不读库）

P3-17 点名的缺口：`synthesize` 与 SSE 主链路零覆盖 —— 而 P0-2/P1-4/P1-5 全在这片区域。
SSE 是最容易"静默坏掉"的地方：事件没推、结束信号没送，前端就无限转圈，
但后端日志一切正常（用户不说就发现不了）。这里从 HTTP 层把事件序列钉死。

用真图 + 假 LLM 跑，是为了让路由接线（条件边、on_event 回调、异常兜底）也被覆盖，
只 mock 掉数据库与记忆（它们跟"事件流怎么走"无关）。
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.deps import get_current_user
from backend.api.routes import chat as chat_route
from backend.db.models.user import User
from tests.conftest import FakeLLM

INTENT = '{"intent": "analysis"}'
PLAN = '[{"id": "t1", "task": "查销售"}]'


def _parse_sse(text: str) -> list[dict]:
    """SSE 正文 → 事件列表（只取 data: 行，忽略空行分隔）"""
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def client(monkeypatch):
    """覆盖鉴权 + 旁路数据库/记忆：这条测试只关心事件流"""
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="tester")

    async def _noop(*a, **k):
        return None

    async def _empty(*a, **k):
        return ""

    monkeypatch.setattr(chat_route, "get_messages", _empty)
    monkeypatch.setattr(chat_route, "recall_user_memories", _empty)
    monkeypatch.setattr(chat_route, "get_uploaded_docs", _empty)
    monkeypatch.setattr(chat_route, "save_message", _noop)
    monkeypatch.setattr(chat_route, "upsert_session", _noop)
    monkeypatch.setattr(chat_route, "_extract_and_save", _noop)   # 后台提炼不碰真 LLM/DB
    yield TestClient(app)
    app.dependency_overrides.clear()


def _use_llm(monkeypatch, script):
    monkeypatch.setattr(chat_route, "create_llm", lambda: FakeLLM(script))


def test_stream_emits_full_event_sequence(client, monkeypatch):
    """主链路事件必须齐全且有序：意图 → 计划 → 子任务 → 逐字 → 完整报告 → 用量 → 会话"""
    _use_llm(monkeypatch, [INTENT, PLAN, "销售环比下降 8%"])
    res = client.post("/api/chat/stream",
                      json={"message": "这周为什么掉量", "session_id": "s1"})

    assert res.status_code == 200
    events = _parse_sse(res.text)
    types = [e["type"] for e in events]

    assert types[0] == "intent"
    for t in ("plan", "subtask", "token", "report", "usage"):
        assert t in types, f"缺少 {t} 事件：{types}"
    assert types[-1] == "session"
    assert types[-2] == "usage"            # 用量必须在结束信号之前推，否则前端收不到
    assert events[-1]["session_id"] == "s1"

    report = [e for e in events if e["type"] == "report"][0]["report"]
    assert report == "假流式"              # FakeLLM 的流式产出


def test_stream_error_event_still_closes(client, monkeypatch):
    """链路炸了也必须把 error + 结束信号送出去 —— 否则 SSE 永远挂起、前端无限转圈"""
    class _Boom:
        async def invoke(self, state):
            raise RuntimeError("图炸了")

    monkeypatch.setattr(chat_route, "build_supervisor", lambda *a, **k: _Boom())
    res = client.post("/api/chat/stream",
                      json={"message": "这周为什么掉量", "session_id": "s1"})

    events = _parse_sse(res.text)
    assert [e["type"] for e in events] == ["error", "usage", "session"]
    assert "图炸了" in events[0]["message"]


def test_budget_exhausted_degrades_but_stream_closes(client, monkeypatch):
    """预算打光不是故障：报告降级、usage 如实标 exhausted、流照样正常结束"""
    monkeypatch.setattr(chat_route.settings, "agent_budget_tokens", 1)
    _use_llm(monkeypatch, [INTENT, PLAN, "销售环比下降 8%"])
    res = client.post("/api/chat/stream",
                      json={"message": "这周为什么掉量", "session_id": "s1"})

    events = _parse_sse(res.text)
    assert [e["type"] for e in events][-1] == "session"
    usage = [e for e in events if e["type"] == "usage"][0]["usage"]
    assert usage["exhausted"] == "tokens"
    report = [e for e in events if e["type"] == "report"][0]["report"]
    assert "预算" in report                # 降级文案，不是空串也不是异常


def test_chat_returns_usage(client, monkeypatch):
    """非流式接口也把用量带回去：这条路的调用方（脚本/其他前端）同样能看见成本"""
    _use_llm(monkeypatch, [INTENT, PLAN, "销售环比下降 8%"])
    res = client.post("/api/chat", json={"message": "这周为什么掉量", "session_id": "s1"})

    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "假流式"
    assert body["usage"]["calls"] >= 3 and body["usage"]["total_tokens"] > 0
    assert body["usage"]["exhausted"] == ""
