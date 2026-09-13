"""
通义千问 LLM 实现 —— 对接阿里云百炼「OpenAI 兼容模式」端点

为什么用兼容模式而不是 dashscope SDK：
- 2026 年的 qwen3.7 系列新模型只挂在兼容端点
  （compatible-mode/v1/chat/completions），dashscope SDK 的旧端点不认识它们
  （调 qwen3.7-plus 会报 400 url error，而兼容端点返回 200）
- OpenAI 兼容格式是业界通用标准，以后换任意 OpenAI 格式的模型（DeepSeek 等）都无缝

获取 API Key：https://bailian.console.aliyun.com/
API 文档：https://help.aliyun.com/zh/model-studio/
"""
import asyncio
import json
import logging
from typing import Optional, AsyncIterator

import requests
import urllib3

from .base import BaseLLM, Message, LLMResponse, ToolCall, TokenUsage

logger = logging.getLogger(__name__)


class QwenLLM(BaseLLM):
    """通义千问大模型实现（OpenAI 兼容端点版）"""

    # OpenAI 兼容端点：POST 一个 chat/completions 请求即可，返回标准 OpenAI 格式
    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    def __init__(self, api_key: str, model: str = "qwen3.7-plus", verify_ssl: bool = True):
        self.api_key = api_key
        self.model = model
        # 只影响本客户端发出的请求，不碰 requests 全局行为（曾经的 monkey-patch 已移除）
        self.verify_ssl = verify_ssl
        if not verify_ssl:
            logger.warning("LLM 请求已关闭 TLS 证书校验（verify_ssl=False），仅限本机调试，请勿带上线")
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ==================== 私有辅助方法 ====================

    def _headers(self) -> dict:
        """OpenAI 兼容端点用 Bearer Token 认证"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _to_qwen_messages(self, messages: list[Message]) -> list[dict]:
        """把我们的 Message 转成 OpenAI 兼容格式字典

        注意要带上 tool_calls / tool_call_id —— ReAct 循环里
        assistant 的 tool_calls 和 tool 结果必须关联，否则 LLM 看不懂工具反馈
        """
        result = []
        for msg in messages:
            item = {"role": msg.role, "content": msg.content}
            # tool 消息：标记这条工具结果是回应哪个调用
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            # assistant 消息：声明要调用哪些工具（OpenAI 格式）
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            result.append(item)
        return result

    def _parse_response(self, resp_json: dict) -> LLMResponse:
        """把 OpenAI 兼容模式的 JSON 返回 → 转成统一 LLMResponse"""
        choices = resp_json.get("choices") or []
        if not choices:
            return LLMResponse(content="")  # 没有 choices，视为空回复

        message = choices[0].get("message") or {}
        content = message.get("content") or ""

        # 解析工具调用（OpenAI 格式：function.arguments 是 JSON 字符串）
        tool_calls = None
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                fn = tc.get("function", {})
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = {}  # 参数不是合法 JSON 时兜底为空 dict
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=arguments,
                ))

        # 解析 token 用量（OpenAI 命名：prompt_tokens / completion_tokens）
        usage = None
        raw_usage = resp_json.get("usage")
        if raw_usage:
            usage = TokenUsage(
                prompt_tokens=raw_usage.get("prompt_tokens") or 0,
                completion_tokens=raw_usage.get("completion_tokens") or 0,
            )

        return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)

    # ==================== 核心方法 ====================

    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """单次对话 —— 等千问完整回复后一次性返回"""
        # 1. 格式转换：我们的 Message → OpenAI 兼容格式
        qwen_messages = self._to_qwen_messages(messages)
        payload = {
            "model": self.model,
            "messages": qwen_messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        # 2. 发同步 HTTP 请求（requests 是阻塞的，必须丢进线程池跑，
        #    否则会阻塞事件循环 —— 多个 executor 并行时 LLM 调用会互相排队）
        def _sync_call() -> dict:
            resp = requests.post(
                self.BASE_URL, headers=self._headers(), json=payload, timeout=(10, 300),
                verify=self.verify_ssl,
            )
            if resp.status_code != 200:
                # 不静默返回空：API 报错必须暴露，否则排查时一头雾水
                raise RuntimeError(
                    f"DashScope API {resp.status_code}: {self._extract_error(resp)}"
                )
            return resp.json()

        resp_json = await asyncio.to_thread(_sync_call)

        # 3. 兼容模式 JSON → 统一的 LLMResponse
        return self._parse_response(resp_json)

    async def chat_stream(
        self,
        messages: list[Message],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """流式对话 —— 每生成一个 token 就 yield 出去（SSE 格式）"""
        qwen_messages = self._to_qwen_messages(messages)
        payload = {
            "model": self.model,
            "messages": qwen_messages,
            "temperature": temperature,
            "stream": True,   # ← 关键：开启流式
        }
        if tools:
            payload["tools"] = tools

        # Queue 桥接：同步线程逐行读 SSE，异步协程从 Queue 取 token 往外推
        q: asyncio.Queue[str] = asyncio.Queue()

        def _sync_stream() -> None:
            try:
                resp = requests.post(
                    self.BASE_URL, headers=self._headers(), json=payload,
                    stream=True, timeout=(10, 300), verify=self.verify_ssl,
                )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"DashScope API {resp.status_code}: {self._extract_error(resp)}"
                    )
                # SSE 每行形如：data: {"choices":[{"delta":{"content":"..."}}]}
                for line in resp.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":  # 流式结束信号
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue  # 跳过解析失败的行
                    delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        q.put_nowait(content)  # 把增量 token 推给协程
            except Exception as e:
                q.put_nowait(None)  # 先让消费者结束，异常在 await task 时抛出
                raise e
            finally:
                q.put_nowait(None)  # None 当结束信号

        task = asyncio.create_task(asyncio.to_thread(_sync_stream))

        while True:
            token = await q.get()
            if token is None:
                break
            yield token

        await task  # 收集线程里的异常（API 报错时这里会抛 RuntimeError）

    def _extract_error(self, resp) -> str:
        """从失败响应里抠错误信息"""
        try:
            return resp.json().get("message", resp.text)
        except Exception:
            return resp.text
