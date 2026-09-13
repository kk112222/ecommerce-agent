"""ReAct Agent —— 自主规划 + 多轮工具调用 + 循环执行"""
from typing import AsyncIterator

from backend.core.llm.base import Message


def _result_text(result) -> str:
    """工具结果 → 喂给 LLM 的文本

    失败时必须带上 error：否则 LLM 只看到 data（None），
    不知道失败原因，下一轮没法自我纠正（参数传错也改不回来）。
    """
    if result.success:
        return str(result.data)
    return f"调用失败：{result.error}"


class ReActAgent:
    """ReAct（Reasoning + Acting）循环 Agent —— LLM 自主决策调用多少次工具"""

    def __init__(self, llm, registry, max_rounds: int = 10):
        self.llm = llm
        self.registry = registry
        self.max_rounds = max_rounds

    async def run(self, messages: list[Message]) -> str:
        """普通模式：循环调工具直到 LLM 认为任务完成"""
        tool_specs = self.registry.get_all_specs()

        for _ in range(self.max_rounds):
            response = await self.llm.chat(messages, tools=tool_specs)

            if not response.tool_calls:
                # LLM 没调工具 → 任务完成，返回文本
                return response.content

            # LLM 调了工具 → 记录 assistant 消息
            messages.append(Message(
                role="assistant", content="", tool_calls=response.tool_calls,
            ))

            # 逐个执行工具
            for tc in response.tool_calls:
                result = await self.registry.execute(tc.name, **tc.arguments)
                messages.append(Message(
                    role="tool", content=_result_text(result), tool_call_id=tc.id,
                ))

            # LLM 会自动从消息历史看到工具结果，不需要注入

        # 超过最大轮数，强制 LLM 总结
        response = await self.llm.chat(messages)
        return response.content

    async def run_stream(self, messages: list[Message]) -> AsyncIterator[dict]:
        """流式模式：循环调工具（非流式），最后回复流式输出"""
        tool_specs = self.registry.get_all_specs()

        yield {"type": "status", "content": "正在分析你的目标，规划执行步骤..."}

        for round_num in range(self.max_rounds):
            response = await self.llm.chat(messages, tools=tool_specs)

            if not response.tool_calls:
                # LLM 认为任务完成 → 流式输出最终回复
                yield {"type": "status", "content": "正在生成最终回复..."}
                async for chunk in self.llm.chat_stream(messages, tools=tool_specs):
                    yield {"type": "token", "content": chunk}
                yield {"type": "done"}
                return

            # LLM 调了工具
            messages.append(Message(
                role="assistant", content="", tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls:
                yield {"type": "status", "content": f"第{round_num + 1}步：调用 {tc.name}..."}
                result = await self.registry.execute(tc.name, **tc.arguments)
                text = _result_text(result)
                yield {
                    "type": "tool_result",
                    "tool": tc.name,
                    "data": text,
                }
                messages.append(Message(
                    role="tool", content=text, tool_call_id=tc.id,
                ))

        # 超最大轮数，强制总结
        yield {"type": "status", "content": "达到最大执行轮数，正在总结..."}
        async for chunk in self.llm.chat_stream(messages, tools=tool_specs):
            yield {"type": "token", "content": chunk}
        yield {"type": "done"}
