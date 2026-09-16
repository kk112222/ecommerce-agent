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

**一个通俗类比**：把 `BaseLLM` 想成 **USB-C 接口标准**，你的 Agent 就是手机。只要充电头支持 USB-C，管它是原装、第三方还是快充协议，插上就能用。换模型 = 换充电头，手机（Agent）完全不用改。工厂模式就是那个"帮你挑充电头"的插线板。

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

抽象定好了，接下来写真正"干活"的代码 —— `backend/core/llm/qwen.py` 里的 `QwenLLM`。

**先说一段踩坑后改掉的实现**（面试很值得讲）：最早用的是 dashscope SDK 的 `Generation.call`。后来想升级到 qwen3.7 系列新模型，结果**全部返回空**，SDK 报 `400 url error`，一度以为是模型名写错了。真相是：**dashscope SDK 走的是旧端点，不认新模型** —— qwen3.7 系列只挂在 **OpenAI 兼容端点**（`compatible-mode/v1/chat/completions`）上。同一个模型名，两个端点一个 200 一个 400。

所以现在改成 **requests 直连 OpenAI 兼容端点**。这不只是为了调新模型：**OpenAI 兼容格式是业界事实标准**，以后换 DeepSeek / 智谱，改 `BASE_URL` + `model` 就行。

它要做三件事：**把我们的格式翻译成 OpenAI 格式**，**发请求**，**再把返回翻译回来**。

### 5.1 格式转换：我们的 Message → OpenAI 格式字典

```python
def _to_qwen_messages(self, messages: list[Message]) -> list[dict]:
    """把我们的 Message 转成 OpenAI 兼容格式字典

    注意要带上 tool_calls / tool_call_id —— ReAct 循环里
    assistant 的 tool_calls 和 tool 结果必须关联，否则 LLM 看不懂工具反馈
    """
    result = []
    for msg in messages:
        item = {"role": msg.role, "content": msg.content}
        if msg.tool_call_id:                       # tool 消息：回应的是哪次调用
            item["tool_call_id"] = msg.tool_call_id
        if msg.tool_calls:                         # assistant 消息：要调哪些工具
            item["tool_calls"] = [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.name,
                             "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
            } for tc in msg.tool_calls]
        result.append(item)
    return result
```

> 比"我们的 `Message` 有 4 个字段、对面只要 2 个"时复杂：**OpenAI 协议要求"请求调用"和"结果返回"成对出现**，所以 `tool_calls` 和 `tool_call_id` 一个都不能丢（丢了 API 直接 400）。第 04 章讲 ReAct 循环时会再遇到它。

### 5.2 响应解析：OpenAI 的返回 → 统一的 LLMResponse

```python
def _parse_response(self, resp_json: dict) -> LLMResponse:
    choices = resp_json.get("choices") or []
    if not choices:
        return LLMResponse(content="")             # 没有 choices，视为空回复

    message = choices[0].get("message") or {}
    content = message.get("content") or ""

    # 工具调用：OpenAI 格式的 function.arguments 是 JSON 字符串
    tool_calls = None
    raw_tool_calls = message.get("tool_calls")
    if raw_tool_calls:
        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}                     # 参数不是合法 JSON 时兜底为空
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""),
                                       arguments=arguments))

    usage = None
    raw_usage = resp_json.get("usage")
    if raw_usage:
        # OpenAI 命名：prompt_tokens / completion_tokens
        usage = TokenUsage(prompt_tokens=raw_usage.get("prompt_tokens") or 0,
                           completion_tokens=raw_usage.get("completion_tokens") or 0)

    return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)
```

一路用 `.get()` 而不是 `[]`：**LLM 返回的字段可能整块缺失**（没有工具调用、没有 usage），`.get()` 天然容错，比 `try / except KeyError` 干净。

### 5.3 核心方法：chat 与 chat_stream

```python
async def chat(self, messages, tools=None, temperature=0.7) -> LLMResponse:
    payload = {"model": self.model, "messages": self._to_qwen_messages(messages),
               "temperature": temperature, "stream": False}
    if tools:
        payload["tools"] = tools

    # requests 是阻塞的，必须丢进线程池 —— 否则多个子任务并行时 LLM 调用会互相排队
    def _sync_call() -> dict:
        resp = requests.post(self.BASE_URL, headers=self._headers(), json=payload,
                             timeout=(10, 300))
        if resp.status_code != 200:
            # 不静默返回空：API 报错必须暴露，否则排查时一头雾水
            raise RuntimeError(f"DashScope API {resp.status_code}: {self._extract_error(resp)}")
        return resp.json()

    resp_json = await asyncio.to_thread(_sync_call)
    return self._parse_response(resp_json)
```

流式版的手法叫 **Queue 桥接**：同步线程逐行读 SSE，异步协程从 Queue 取 token 往外推。

```python
async def chat_stream(self, messages, tools=None, temperature=0.7) -> AsyncIterator[str]:
    q: asyncio.Queue[str] = asyncio.Queue()

    def _sync_stream() -> None:
        resp = requests.post(..., stream=True, timeout=(10, 300))
        for line in resp.iter_lines(decode_unicode=True):   # SSE 每行：data: {...}
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":                            # 流式结束信号
                break
            delta = (json.loads(data).get("choices") or [{}])[0].get("delta") or {}
            if delta.get("content"):
                q.put_nowait(delta["content"])
        q.put_nowait(None)                                  # None 当结束信号

    task = asyncio.create_task(asyncio.to_thread(_sync_stream))
    while True:
        token = await q.get()
        if token is None:
            break
        yield token
    await task          # 收集线程里的异常（API 报错时在这里抛出来）
```

> 为什么流式比普通版麻烦这么多？因为 `requests` 的流式读取是**同步阻塞**的，而 `chat_stream` 必须是**异步生成器**。**Queue 就是"同步世界"和"异步世界"之间的桥** —— 这套手法在第 06 章推 SSE 时还会再用一次。

### 5.4 踩过的坑（本文件真实记录）

| 坑 | 现象 | 解法 |
|---|---|---|
| **旧端点不认新模型** | qwen3.7 全部返回空，SDK 报 `400 url error`，误以为模型名不存在 | 改用 **OpenAI 兼容端点**；**报错要信第一手信息** —— 用 requests 直连两个端点对比，200 的才是对的 |
| **静默吞错** | `_parse_response` 返回空串，把 400 吞成了"模型返回空"，排查走了大弯路 | 非 200 **直接抛 `RuntimeError`**，错误不再被吞 |
| **requests 阻塞事件循环** | 同步 HTTP 卡住整个 asyncio，并行子任务互相排队 | 丢进 `asyncio.to_thread` 线程池 |
| **Windows SSL 证书问题** | 开发环境请求失败，报证书验证错误 | 临时全局 `verify=False`，代码里注释了"上线前务必删除" |

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

写抽象层不能光看代码，跑起来才算数。`scripts/smoke_llm.py` 同时测了普通对话和流式对话：

```bash
python scripts/smoke_llm.py
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
- **踩坑**：dashscope 旧端点不认新模型（改走 OpenAI 兼容端点）、静默吞错、requests 阻塞事件循环、Windows SSL

---

## 练手挑战

> 光看不练假把式。这三题从易到难，做完比看十遍都懂。
> 不用怕 —— 前两题本质就是把本章思路**倒着实现一遍**。

**挑战一（必做）：写一个 FakeLLM**

不调任何真实 API，自己实现一个假模型，拿它跑通整条链路。

```python
class FakeLLM(BaseLLM):
    async def chat(self, messages, tools=None, temperature=0.7):
        return LLMResponse(content="我是假的，但我能跑通链路！")

    async def chat_stream(self, messages, tools=None, temperature=0.7):
        for ch in "我是假的，但我能跑通链路！":
            yield ch
```

**验收标准**：写个 `test_fake.py`，调 `await llm.chat([...])` 能拿到写死的回答，流式能逐字吐。

**挑战二（推荐）：把 FakeLLM 接进工厂**

在 `factory.py` 加一个分支，让 `.env` 里 `LLM_PROVIDER=fake` 就能切过去。

**验收标准**：改 `.env` 后跑 `scripts/smoke_llm.py`，不再打真 API、直接输出固定文案 —— 证明"加实现 + 加分支，业务零改动"不是吹的。

**挑战三（加分）：接第二家真模型**

很多国产模型（DeepSeek、智谱）提供 OpenAI 兼容接口。写一个 `OpenAIChatLLM` 继承 `BaseLLM`，实现 `chat` / `chat_stream`，再把它加进工厂。

**提示**：照抄 `QwenLLM` 的结构就行，重点对比各家**流式返回的字段差异**。

**验收标准**：填上真 key，普通对话 + 流式都能正常出字。

> 做完挑战一、二，你就亲手把"为什么上层只依赖抽象"体会了一遍；做完挑战三，你就有资格说"我会写 LLM 抽象层"了。

---

## 下一步

LLM 能对话了，但光会聊天没用。第二章看**工具系统** —— 怎么让 Agent 能"调用工具、查数据"，这是它从"聊天机器人"变成"会干活"的关键一步。

→ [02 工具系统](02-工具系统.md)
