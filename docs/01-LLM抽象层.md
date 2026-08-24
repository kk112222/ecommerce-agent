# 01 · LLM 抽象层

> 项目一切能力的起点是 LLM。这一章要解决一个问题：
> **怎么让"用哪个大模型"变成一行配置，而不是全局搜索替换。**

---

## 本章目标

读完这一章，你会明白：

1. 业务代码**为什么不能直接调**大模型 API
2. 什么是**依赖倒置**，以及它在本项目里怎么落地
3. 统一的**数据结构**是怎么屏蔽不同厂商 API 差异的
4. 抽象接口 `BaseLLM` 定义了哪些能力
5. 工厂模式如何做到"**一行切换模型**"

---

## 一、先看痛点：直接调 API 会发生什么

写业务代码时最朴素的想法，就是在需要的地方直接调 SDK：

```python
# ❌ 反例：业务代码里直接调千问
from dashscope import Generation

def ask_sales(user_input: str):
    response = Generation.call(
        model="qwen-max",
        messages=[{"role": "user", "content": user_input}],
    )
    return response.output.choices[0].message.content
```

这看起来能用，但埋了三个雷：

| 问题 | 后果 |
|---|---|
| **深度耦合** | 业务代码和千问 SDK 绑死。将来想换 DeepSeek / OpenAI，改的不是一处，而是每个调用点 |
| **格式各不同** | 千问返回的结构、工具调用格式、token 统计字段，和别家都不一样，适配代码散落各处 |
| **没法测试** | 一跑就真调 API，没 key 就没法开发，也不好 mock |

> 核心思路：**不依赖具体的"千问"，依赖抽象的"LLM"** —— 这就是依赖倒置。

---

## 二、设计思路：依赖倒置（DIP）

依赖倒置原则（Dependency Inversion Principle）说得很抽象，翻译成人话就是：

> **高层模块（Agent）不要依赖低层模块（具体某个模型），两边都依赖一个"抽象接口"。**

放在本项目就是：

```
        依赖抽象，不依赖实现
    ┌─────────────────────────┐
    │        Agent 层          │
    └────────────┬────────────┘
                 │  只认识 BaseLLM
                 ▼
            BaseLLM（抽象接口）
                 ▲
                 │  实现它
        ┌────────┴────────┐
        │                 │
    QwenLLM            OpenAIChatLLM（以后加）
```

好处立竿见影：

- **Agent 只写一遍**，永远不用关心底下到底是谁
- 换模型 = 换一个实现，业务代码**零改动**
- 测试时能轻松替换成"假 LLM"，不花钱不打真 API

---

## 三、统一数据结构：屏蔽 API 差异

各家 API 的消息格式、返回结构都不一样。所以第一步，先定义一套**自己的数据结构**，把差异挡在外面。

代码在 `backend/core/llm/base.py`，一共四个数据类：

```python
class Message(BaseModel):
    """统一消息格式 —— 屏蔽不同 LLM API 的差异"""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str  # 消息文本
    tool_call_id: Optional[str] = None  # tool消息专用，关联到哪个调用
    tool_calls: Optional[list[ToolCall]] = None  # assistant消息专用，要调用哪些工具
```

| 数据类 | 一句话解释 |
|---|---|
| `Message` | 对话里的**每一条消息**，统一的 role + content |
| `ToolCall` | 模型发起的一次**工具调用**（id / 工具名 / 参数） |
| `TokenUsage` | **token 用量**统计（prompt + completion） |
| `LLMResponse` | **统一的返回** —— 不管底层是谁，返回结构都一样 |

> 这四个类就是整个 LLM 层的"通用语言"。上层 Agent 只跟这套语言打交道。

---

## 四、抽象基类：只定义"能干什么"

有了数据结构，再定义**接口**。`BaseLLM` 是个抽象基类，声明了两件事：

```python
class BaseLLM(ABC):
    """LLM 抽象接口 —— 所有模型实现必须继承此类"""

    @abstractmethod
    async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
        """单次对话 —— 等 LLM 完整返回后再继续"""
        ...

    @abstractmethod
    async def chat_stream(self, messages, tools=None, temperature=0.7) -> AsyncIterator[str]:
        """流式对话 —— 每生成一个 token 就 yield 出去（SSE 推送用）"""
        ...
```

注意几个设计点：

1. **只声明"能干什么"，不写"怎么干"** —— `@abstractmethod` 强制子类必须实现
2. **两个方法**：`chat` 等完整回复（适合后台分析），`chat_stream` 逐字吐（适合前端展示）
3. **统一带 `tools` 参数** —— 这是 Function Calling 的入口，为第二章工具系统留好了位置

> 对上层来说，接口只有两个方法，**简单到不能再简单**。复杂度全被压到实现类里了。

---

## 五、具体实现：QwenLLM 对接 DashScope

抽象定好了，接下来写真正的"干活"代码 —— `backend/core/llm/qwen.py` 里的 `QwenLLM`。

它要做三件事：**把自己的格式翻译成千问的**，**调 API**，**再把千问的返回翻译回来**。

### 5.1 格式转换：我们的 Message → 千问的字典

```python
def _to_qwen_messages(self, messages: list[Message]) -> list[dict]:
    """把我们的 Message 转成千问 API 需要的字典"""
    result = []
    for msg in messages:
        result.append({"role": msg.role, "content": msg.content})
    return result
```

我们的 `Message` 是 pydantic 对象，千问要的是纯字典。做个薄薄的转换即可。

### 5.2 响应解析：千问的返回 → 统一的 LLMResponse

千问返回结构很深（`response.output.choices[0].message`），而且**字段可能不存在**，直接取会抛 `KeyError`。所以解析时做了兜底：

```python
def _parse_response(self, response) -> LLMResponse:
    output = response.output
    if output and output.choices:
        msg = output.choices[0].message
        content = msg.content or ""

        tool_calls = None
        try:
            raw_tool_calls = msg.tool_calls
            if raw_tool_calls:
                tool_calls = [ToolCall(...) for tc in raw_tool_calls]
        except (KeyError, AttributeError):
            pass  # 没有工具调用，保持 None

        return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)
    return LLMResponse(content="")  # API 异常时返回空
```

### 5.3 核心方法：chat 与 chat_stream

```python
async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
    qwen_messages = self._to_qwen_messages(messages)
    response = Generation.call(
        api_key=self.api_key,
        model=self.model,
        messages=qwen_messages,
        tools=tools,
        result_format="message",   # 要求返回标准 message 结构
        temperature=temperature,
    )
    return self._parse_response(response)
```

流式版只多两行，也是将来 SSE 推送的关键：

```python
async def chat_stream(self, messages, tools=None, temperature=0.7) -> AsyncIterator[str]:
    ...
    responses = Generation.call(
        ...
        stream=True,              # ← 关键：开启流式
        incremental_output=True,  # ← 增量输出，每次只推新内容
    )
    for response in responses:
        content = response.output.choices[0].message.content
        if content:
            yield content
```

> `incremental_output=True` 很关键：不开它，千问会每次重推**全部内容**；开了它，每次都只推**新增的那一小段**，省流量也省前端拼接的麻烦。

### 5.4 踩过的坑（本文件真实记录）

| 坑 | 现象 | 解法 |
|---|---|---|
| **Windows SSL 证书问题** | 开发环境请求失败，报证书验证错误 | 临时全局关闭 `verify=False`，代码里注释了"上线前务必删除" |
| **字段不存在抛 KeyError** | 千问没返回工具调用 / usage 时，`msg.tool_calls` 取不到 | 用 `try / except (KeyError, AttributeError)` 兜底 |
| **返回结构不对** | 不加 `result_format="message"` 时返回旧格式 | 显式指定 `result_format="message"` |

---

## 六、工厂模式：一行切换模型

抽象和实现都齐了，最后一步是**怎么决定用哪个**。这就是 `backend/core/llm/factory.py`：

```python
def create_llm() -> BaseLLM:
    if settings.llm_provider == "qwen":
        return QwenLLM(
            api_key=settings.dashscope_api_key,
            model=settings.llm_model,
        )
    raise ValueError(f"不支持的 LLM provider: {settings.llm_provider}")
```

配合 `core/config.py`，一切由 `.env` 驱动：

```python
llm_provider: str = "qwen"          # 当前用哪家
llm_model: str = "qwen3-max"        # 用哪个模型
dashscope_api_key: str = ""         # 千问的 key
```

**以后要加 DeepSeek，只需要两步：**

1. 写一个 `DeepSeekLLM(BaseLLM)`，实现 `chat` / `chat_stream`
2. 工厂里加一个分支：`if settings.llm_provider == "deepseek": ...`

业务代码（Agent、API）**一行都不用动** —— 这就是抽象层 + 工厂模式的回报。

> 为什么返回类型要写成 `-> BaseLLM`？因为调用方只认抽象类型，这样将来替换实现时，静态类型检查也能通过。

---

## 七、验证一下

写抽象层不能光看代码，跑起来才算数。`scripts/test_llm.py` 同时测了普通对话和流式对话：

```bash
python scripts/test_llm.py
```

```text
=== 普通对话 ===
回复: 我是通义千问，由阿里云研发的大语言模型...

=== 流式对话 ===
回复: 我是通义千问，由阿里云研发的大语言模型...
```

普通对话一次性返回；流式对话是**一个字一个字蹦出来**的（在终端看效果更直观）。这段脚本里还藏了个小坑：Windows 终端是 GBK 编码，打印模型返回的字符可能报错，所以包装了个 `safe()` 函数做编码兜底。

---

## 本章小结

- **依赖倒置**：Agent 依赖 `BaseLLM` 抽象，不依赖具体模型
- **统一数据结构**：`Message` / `ToolCall` / `TokenUsage` / `LLMResponse` 挡住 API 差异
- **抽象基类**：只声明 `chat` / `chat_stream`，实现细节下沉到实现类
- **工厂模式**：`create_llm()` 一行切换，换模型不动业务代码
- **踩坑**：Windows SSL、千问字段 `KeyError`、`result_format="message"`

---

## 下一步

LLM 能对话了，但光会聊天没用。第二章看**工具系统** —— 怎么让 Agent 能"调用工具、查数据"，这是它从"聊天机器人"变成"会干活"的关键一步。

→ [02 工具系统](02-工具系统.md)
