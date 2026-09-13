"""文档 Agent 全链路（离线）：ReAct 循环 → 工具调用 → 落盘 → 回传路径

用假 LLM 驱动一次真实的 "调 write_document" 决策，验证：
工具注册中心 + 上下文注入（user/session）+ 路径安全落盘 是通的。
"""
import pytest

from backend.agents.document import DocumentAgent
from backend.core.llm.base import LLMResponse, ToolCall
from backend.core.tool.registry import ToolRegistry
from backend.infrastructure import doc_output
from backend.tools import register_all_tools
from tests.conftest import FakeLLM


@pytest.fixture(autouse=True)
def _isolate_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_output, "OUTPUTS_DIR", tmp_path)
    yield


async def test_document_agent_writes_file(tmp_path):
    llm = FakeLLM([
        LLMResponse(content="", tool_calls=[ToolCall(
            id="c1", name="write_document",
            arguments={"filename": "竞品报告", "content": "# 竞品分析\n价差 20 元",
                       "format": "docx"})]),
        LLMResponse(content="已生成文档，路径见工具结果。"),
    ])
    registry = ToolRegistry()
    register_all_tools(registry, llm, context={"user_id": 7, "session_id": "s"})

    out = await DocumentAgent(llm, registry).run(
        goal="生成一份竞品分析报告", uploaded_data="我方 T恤 99，对手 79")

    assert "已生成" in out
    # 落盘位置按 user/session 隔离
    assert (tmp_path / "user_7" / "s" / "竞品报告.docx").exists()
    # 上传文档被注入成 system 块，否则 Agent 没原料
    assert any("【上传文档】" in m.content for m in llm.calls[0])


async def test_document_agent_reports_error_on_bad_args():
    """坏参数不崩：registry 返回可读错误 → ReAct 拿到 tool 消息继续（这里以最终回复收尾）"""
    llm = FakeLLM([
        LLMResponse(content="", tool_calls=[ToolCall(
            id="c1", name="write_document", arguments={"filename": "x"})]),   # 缺 content
        LLMResponse(content="参数不完整，我重新调用。"),
    ])
    registry = ToolRegistry()
    register_all_tools(registry, llm, context={"user_id": 7, "session_id": "s"})
    out = await DocumentAgent(llm, registry).run(goal="存个文档")
    assert "重新调用" in out
