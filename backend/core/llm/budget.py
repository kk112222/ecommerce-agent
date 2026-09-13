"""LLM 预算：全局 token / 时间上限 + 用量汇总（P2-11）

问题：intent + planner + 4×executor(每个≤10 轮) + synthesizer，最坏 40+ 次 LLM 调用，
且 ReAct 循环"LLM 自己决定调几次工具"，成本与延迟都没有上限。
TokenUsage 定义了却从没被汇总过 —— 用户看不到花了多少，也没法在上线时控成本。

为什么卡在 LLM 层而不是 Graph 层：调用次数是 ReAct 循环内部攒出来的（planner 看不见、
Graph 节点也看不见），只有包住 BaseLLM 才能覆盖全部调用点 —— 包括工具自己拿 llm 发起的
调用。好处是所有 Agent 一行不改，换模型/加 Agent 都自动受控。

超预算不是崩，是"降级"：BudgetExceeded 由上层（supervisor 各节点）接住，
已跑完的部分照样出报告，末尾注明提前结束。
"""
import time
from typing import AsyncIterator, Optional

from backend.core.llm.base import BaseLLM, LLMResponse, Message, TokenUsage


class BudgetExceeded(Exception):
    """本轮预算用尽 —— 带人话原因，便于直接展示给用户/写进日志"""

    def __init__(self, kind: str, detail: str):
        self.kind = kind          # "tokens" | "time"
        self.detail = detail
        super().__init__(detail)


def estimate_tokens(text: str) -> int:
    """没有 usage 时的粗略估算（流式接口不返回 usage，只能估）

    中文约 1.5 字/token、英文约 4 字符/token。只是量级判断，够用来止损，
    精确值仍以 API 返回的 usage 为准（snapshot 里 estimated_calls 会如实标出估了几次）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF)   # CJK 统一表意文字
    other = len(text) - cjk
    return int(cjk / 1.5) + other // 4 + 1


def _text_of(messages: list[Message]) -> str:
    return "\n".join(m.content or "" for m in messages)


class UsageBudget:
    """一轮对话共享的账本：所有 LLM 调用往里记账，超了就不让再调

    max_tokens / max_seconds 传 0 表示不限制（本地脚本、评测时用）。
    """

    def __init__(self, max_tokens: int = 0, max_seconds: float = 0):
        self.max_tokens = max_tokens
        self.max_seconds = max_seconds
        self.started_at = time.monotonic()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        self.estimated_calls = 0     # 其中没有 usage、靠估算的次数
        self.exhausted = ""          # "" | "tokens" | "time"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def _over(self) -> str:
        """超限类型："" / "tokens" / "time"

        注意语义：上限卡的是"还发不发下一次调用"，所以实际用量最多超出一次调用的量。
        这是刻意选的 —— 已经付过费拿到的回复不能因为超限就丢掉（尤其流式报告，
        半截掐断用户拿到的是残缺内容）。120k 的额度下这点溢出可以忽略。
        """
        if self.max_tokens and self.total_tokens >= self.max_tokens:
            return "tokens"
        if self.max_seconds and self.elapsed >= self.max_seconds:
            return "time"
        return ""

    def check(self) -> None:
        """每次调用 LLM 前检查：已经超了就拒绝这一次，不再往下烧"""
        kind = self._over()
        if not kind:
            return
        self.exhausted = kind
        if kind == "tokens":
            raise BudgetExceeded(
                "tokens", f"本轮已用 {self.total_tokens} tokens，达到上限 {self.max_tokens}")
        raise BudgetExceeded(
            "time", f"本轮已耗时 {self.elapsed:g} 秒，达到上限 {self.max_seconds:g} 秒")

    def record(self, usage: Optional[TokenUsage], prompt_text: str = "",
               completion_text: str = "") -> None:
        """记一次调用。有 usage 用真值，没有（流式）就按文本估算并标记"""
        self.calls += 1
        if usage is not None:
            self.prompt_tokens += usage.prompt_tokens
            self.completion_tokens += usage.completion_tokens
        else:
            self.estimated_calls += 1
            self.prompt_tokens += estimate_tokens(prompt_text)
            self.completion_tokens += estimate_tokens(completion_text)
        # 记账后就标记超限：这样"最后一个调用把额度用超了、之后没再调用"的情况，
        # 也能在最终快照里如实显示 exhausted（否则前端看到的是"没超限"）
        self.exhausted = self._over() or self.exhausted

    def snapshot(self) -> dict:
        """汇总用量 —— 挂到响应里给前端展示，也是排查"这轮为什么慢/贵"的依据"""
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_calls": self.estimated_calls,
            "elapsed_ms": int(self.elapsed * 1000),
            "max_tokens": self.max_tokens,
            "max_seconds": self.max_seconds,
            "exhausted": self.exhausted,
        }


class BudgetedLLM(BaseLLM):
    """带预算的 LLM 装饰器 —— 与具体模型实现无关，套在任意 BaseLLM 外面即可"""

    def __init__(self, inner: BaseLLM, budget: UsageBudget):
        self.inner = inner
        self.budget = budget

    async def chat(self, messages: list[Message], tools: Optional[list[dict]] = None,
                   temperature: float = 0.7) -> LLMResponse:
        self.budget.check()                      # 先问账本还有没有额度
        response = await self.inner.chat(messages, tools=tools, temperature=temperature)
        self.budget.record(response.usage, prompt_text=_text_of(messages),
                           completion_text=response.content or "")
        return response

    async def chat_stream(self, messages: list[Message], tools: Optional[list[dict]] = None,
                          temperature: float = 0.7) -> AsyncIterator[str]:
        self.budget.check()
        chunks: list[str] = []
        async for chunk in self.inner.chat_stream(messages, tools=tools, temperature=temperature):
            chunks.append(chunk)
            yield chunk
        # 流式接口不返回 usage：等流结束按产出文本估算（报告通常最长，不能漏记）
        self.budget.record(None, prompt_text=_text_of(messages),
                           completion_text="".join(chunks))
