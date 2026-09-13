"""工具参数统一校验验证（stub LLM，不联网、不烧配额）

背景：以前每个工具各写 **kwargs + 别名兜底（text/document/original…），
既不可维护，工具失败时的 error 又被 agent 丢掉（只喂 data，LLM 看到 "None"）。
现在校验统一收到 ToolRegistry.execute：按 spec.parameters 的 required 检查入参。

覆盖：
1. 未知工具名 → 可读 error（列出可用工具）
2. 所有 required 非空的工具，零参数调用 → 一律返回 success=False，不抛异常
3. 传错键名（document / prompt）→ 被过滤后触发缺参 error，且 error 带参数说明
4. 多余键混着正确键 → 被过滤，正常执行
5. None / 空串算缺参；0 / False 是合法值，不算缺（fake 工具覆盖）
6. ReAct 全链路：坏参数的 error 真的喂回了 LLM（不再丢成 "None"）

跑完无副作用（全程不落盘）。
"""
import asyncio
import logging
import sys
from pathlib import Path

logging.disable(logging.CRITICAL)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.agents.data_analysis.simple_agent import ReActAgent
from backend.core.llm.base import LLMResponse, Message, ToolCall
from backend.core.tool.base import BaseTool, ToolResult, ToolSpec
from backend.core.tool.registry import ToolRegistry
from backend.tools import register_all_tools


class _StubLLM:
    """按预设序列返回 LLMResponse；记录每轮看到的 messages 快照"""
    def __init__(self, responses):
        self._responses = list(responses)
        self.seen = []

    async def chat(self, messages, tools=None, temperature=0.0):
        self.seen.append(list(messages))
        return self._responses.pop(0) if self._responses else LLMResponse(content="（stub 无更多响应）")

    async def chat_stream(self, messages, tools=None, temperature=0.0):
        yield "（stub 流式）"


class _FakeTool(BaseTool):
    """只为验证 0 / False 是否被误判成缺参"""
    spec = ToolSpec(
        name="fake_tool", description="测试用",
        parameters={
            "type": "object",
            "properties": {"count": {"type": "integer", "description": "数量"},
                           "flag": {"type": "boolean", "description": "开关"}},
            "required": ["count", "flag"],
        },
    )

    # 故意不写 **kwargs：万一路由层没过滤干净，传进来多余的键会直接 TypeError 暴露问题
    async def execute(self, count=None, flag=None):
        return ToolResult(success=True, data=f"count={count} flag={flag}")


def main():
    stub = _StubLLM([LLMResponse(content="（改写后的全文）")])
    reg = ToolRegistry()
    register_all_tools(reg, stub, context={"user_id": 999999, "session_id": "s1"})
    reg.register(_FakeTool())

    # 1) 未知工具名
    r = asyncio.run(reg.execute("no_such_tool", foo=1))
    assert not r.success and "no_such_tool" in r.error and "可" in r.error, r
    print("[1] 未知工具名 ok：", r.error[:60], "...")

    # 2) 所有 required 非空的工具，零参数调用必须被拦下（且不能抛异常）
    names = [n for n in reg.tools
             if (reg.tools[n].spec.parameters or {}).get("required")]
    for name in names:
        r = asyncio.run(reg.execute(name))
        assert not r.success, (name, r)
        assert "缺少必需参数" in r.error, (name, r.error)
    print(f"[2] 零参数拦截 ok：{len(names)} 个工具全部返回可读缺参错误，无一抛异常")

    # 3) 传错键名 —— 就是用户问过的 document / prompt / original / request 那批别名
    r = asyncio.run(reg.execute("optimize_document",
                                document="原文", prompt="精简一点"))
    assert not r.success and "content" in r.error and "instruction" in r.error, r
    print("[3] 传错键名 ok：document/prompt 被过滤 →", r.error[:80], "...")

    # 4) 多余键混着正确键 → 过滤后正常执行
    r = asyncio.run(reg.execute("optimize_document",
                                content="很长很长的原文", instruction="精简",
                                format="md", extra_junk={"a": 1}))
    assert r.success and "改写后" in r.data["optimized"], r
    print("[4] 多余键过滤 ok：工具正常执行 →", r.data["optimized"])

    # 5) None / 空串算缺参；0 / False 不算
    for bad in (None, ""):
        r = asyncio.run(reg.execute("fake_tool", count=bad, flag=False))
        assert not r.success and "count" in r.error, (bad, r)
    r = asyncio.run(reg.execute("fake_tool", count=0, flag=False))
    assert r.success and "count=0 flag=False" in r.data, r
    print("[5] 空值判定 ok：None/空串算缺参，0/False 是合法值")

    # 6) ReAct 全链路：第一轮 LLM 传错键名，第二轮它必须能看到 error 文案
    bad_llm = _StubLLM([
        LLMResponse(content="", tool_calls=[ToolCall(
            id="call_1", name="optimize_document",
            arguments={"document": "原文", "prompt": "精简"})]),
        LLMResponse(content="我看错了参数名，已改用 content/instruction。"),
    ])
    reg2 = ToolRegistry()
    register_all_tools(reg2, bad_llm, context={"user_id": 999999, "session_id": "s1"})
    out = asyncio.run(ReActAgent(bad_llm, reg2).run([Message(role="user", content="精简这段")]))
    assert "参数名" in out, out
    # 注意：stub 同时被工具内层 LLM 调用复用，所以取最后一轮（不是 seen[1]）
    tool_msgs = [m for m in bad_llm.seen[-1] if m.role == "tool"]
    assert tool_msgs and "调用失败" in tool_msgs[0].content, tool_msgs
    assert "缺少必需参数" in tool_msgs[0].content, tool_msgs[0].content
    print("[6] ReAct 链路 ok：error 已喂回 LLM →", tool_msgs[0].content[:70], "...")

    # 7) 成功的工具结果不受影响（仍喂 data，不带"调用失败"前缀）
    # 三个预设响应依次被用掉：ReAct 第 1 轮 → optimize_document 内层调用 → ReAct 第 2 轮
    good_llm = _StubLLM([
        LLMResponse(content="", tool_calls=[ToolCall(
            id="call_2", name="optimize_document",
            arguments={"content": "原文", "instruction": "精简"})]),
        LLMResponse(content="（改写后的全文）"),
        LLMResponse(content="已完成。"),
    ])
    reg3 = ToolRegistry()
    register_all_tools(reg3, good_llm, context={"user_id": 999999, "session_id": "s1"})
    out = asyncio.run(ReActAgent(good_llm, reg3).run([Message(role="user", content="精简这段")]))
    assert out == "已完成。", out
    msg = [m for m in good_llm.seen[-1] if m.role == "tool"][0]
    assert "调用失败" not in msg.content and "改写后" in msg.content, msg.content
    print("[7] 成功路径 ok：喂回的是 data，无失败前缀")

    print("工具参数统一校验验证全绿 ✅")


if __name__ == "__main__":
    main()
