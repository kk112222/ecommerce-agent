from abc import ABC, abstractmethod
from typing import Optional, AsyncIterator
from pydantic import BaseModel


# ==================== 数据结构 ====================

class ToolCall(BaseModel):
    """LLM 发起的工具调用"""
    id: str  # 调用 ID，如 "call_abc123"
    name: str  # 工具名，如 "query_sales"
    arguments: dict  # 参数，如 {"start_date":"2026-07-01"}

class TokenUsage(BaseModel):
    """Token 用量统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Message(BaseModel):
    """统一消息格式 —— 屏蔽不同 LLM API 的差异"""
    role: str  # "system" | "user" | "assistant" |"tool"
    content: str  # 消息文本
    tool_call_id: Optional[str] = None  # tool消息专用，关联到哪个调用
    tool_calls: Optional[list[ToolCall]] = None  # assistant消息专用，要调用哪些工具


class LLMResponse(BaseModel):
    """LLM 统一返回 —— 无论底层是什么模型，返回格式都一样"""
    content: str
    tool_calls: Optional[list[ToolCall]] = None
    usage: Optional[TokenUsage] = None


# ==================== 抽象基类 ====================

class BaseLLM(ABC):
    """
    LLM 抽象接口 —— 所有模型实现必须继承此类

    设计目的：上层 Agent 只依赖 BaseLLM，不依赖具体模型。
    切换模型只需改一行工厂调用，不动业务代码。
    """

    @abstractmethod
    async def chat(
            self,
            messages: list[Message],
            tools: Optional[list[dict]] = None,
            temperature: float = 0.7,
    ) -> LLMResponse:
        """
        单次对话 —— 等 LLM 完整返回后再继续

        messages: 对话历史
        tools: 工具列表的 JSON Schema（OpenAI 格式），没有工具就传None
        temperature: 0=严谨 1=创意
        """
        ...

    @abstractmethod
    async def chat_stream(
            self,
            messages: list[Message],
            tools: Optional[list[dict]] = None,
            temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """
        流式对话 —— 每生成一个 token 就 yield 出去

        用于 SSE 推送给前端，用户看到逐字输出的效果
        """
        ...