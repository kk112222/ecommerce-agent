"""公共测试夹具 —— 全部离线：假 LLM 顶替真模型，不联网、不读数据库、不写真实文件

这正兑现 BaseLLM 抽象的设计红利：上层 Agent 只依赖 BaseLLM 接口，
测试时塞一个 FakeLLM 就能把整条链路跑起来（外部评审说的"换假 LLM 的能力没用上"）。
"""
from backend.core.llm.base import BaseLLM, LLMResponse


class FakeLLM(BaseLLM):
    """按预设脚本依次返回；脚本用尽后返回 default。记录每次收到的 messages 便于断言。"""

    def __init__(self, responses=None, default: str = "（假回复）"):
        self._responses = list(responses or [])
        self.default = default
        self.calls: list[list] = []          # 每次 chat 收到的 messages 快照

    async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
        self.calls.append(list(messages))
        if self._responses:
            r = self._responses.pop(0)
            return r if isinstance(r, LLMResponse) else LLMResponse(content=r)
        return LLMResponse(content=self.default)

    async def chat_stream(self, messages, tools=None, temperature=0.7):
        self.calls.append(list(messages))
        for chunk in ("假", "流式"):
            yield chunk
