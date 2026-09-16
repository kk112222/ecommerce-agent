# 04 · Agent 引擎

> 前三章备好了三块料：**LLM 会思考**、**工具有手**、**数据有地方存**。
> 但没人把它们串起来 —— 谁来决定"先查哪个、再查哪个、查到什么时候停"？
> 这一章写引擎：**48 行的状态图 + 一个 while 循环**。

---

## 本章目标

读完这一章，你会明白：

1. `AgentGraph` 这 48 行到底干了什么（以及为什么这么短就够用）
2. ReAct 循环的四步是怎么转起来的
3. **为什么 `tool_calls` 和 `tool_call_id` 必须成对**（丢一个就 400）
4. 工具失败时，怎么让 LLM **自己纠正**而不是直接崩
5. 为什么"轮数上限"是必须的，超了怎么收场

---

## 一、先看痛点：手上有料，但没有"流水线"

到第 03 章为止，项目里已经能手动跑通这么一段：

```python
resp = await llm.chat(messages, tools=registry.get_all_specs())
if resp.tool_calls:
    for tc in resp.tool_calls:
        result = await registry.execute(tc.name, **tc.arguments)
```

**能跑通一次工具调用**，但只跑一次。真实问题是：

| 问题 | 说明 |
|---|---|
| **要调几次？** | 用户问"这个月销售为什么跌了"——可能要查销售、再查库存、再看会员结构，**次数事前不知道** |
| **什么时候停？** | 不能无限查下去，得有终止条件 |
| **查完了谁总结？** | 工具返回的是一堆 JSON，得让 LLM 把它说成人话 |

一句话：**需要一条"能循环、能判断、能停"的流水线**。

项目自己写了两层：

```
AgentGraph（编排层）  —— 节点之间怎么走：谁接谁、走哪条分支
    └─ ReActAgent（执行层）—— 单个节点内部：调工具 → 看结果 → 再决定
```

> 记住这个分工：**节点之间靠图，节点之内靠循环**。第 05 章的 supervisor 就是把图铺开成多 Agent 编排。

---

## 二、48 行的状态图引擎

引擎在 `backend/core/agent/base.py`，全文 48 行。核心就一个思路：**状态是一个字典，节点是函数，节点之间用边连起来**。

### 2.1 四个概念

```python
class AgentGraph:
    def __init__(self):
        self.nodes: dict[str, Callable] = {}     # 节点名 → 函数
        self.edges: list[tuple[str, str]] = []   # 普通边：(from, to)
        self.conditions: dict[str, Callable] = {}  # 条件边：from → 路由函数
        self.entry_point: str = ""               # 起始节点
        self.interrupt_before: set[str] = set()  # 暂停点
```

对照理解：

| 概念 | 类比 | 在本项目里是什么 |
|---|---|---|
| `state` | 传送带上的托盘 | 一个 `dict`，塞 `messages` / `intent` / `plan` 随便什么 key |
| `node` | 工位 | 一个函数，收 state、加工、返回 state |
| `edge` | 固定传送带 | 无条件跳转：A 跑完必定去 B |
| `condition` | 分拣岔道 | 根据 state 决定去 B 还是 C |

### 2.2 注册节点与边

```python
def add_node(self, name: str, func: Callable):
    self.nodes[name] = func

def add_edge(self, start: str, end: str):
    self.edges.append((start, end))          # 就是个列表，不是邻接表

def add_condition_edges(self, source: str, router: Callable, mapping: dict[str, str]):
    """source 跑完后，router(state) 的返回值当作 key 去 mapping 里查下一个节点"""
    self.conditions[source] = lambda state: mapping[router(state)]
```

条件边这里有个**很巧的间接层**：调用方给的不是"下一步是谁"，而是"**路由函数 + 一张对照表**"。

```python
graph.add_condition_edges(
    "intent", router,
    {"analysis": "planner", "content": "content_agent"},
)
```

`router(state)` 返回 `"analysis"` → 查表 → 去 `planner`。好处是**路由逻辑和目标是分开的**：换目标不用改路由函数，改表就行。

> 代价：路由函数返回了表里没有的 key 会直接 `KeyError`。所以路由函数的取值必须和表**一一对应**（第 05 章的 `intent_router` 就为这个写了兜底默认值）。

### 2.3 执行：一个 while 循环

```python
async def invoke(self, state: State) -> State:
    current = self.entry_point

    while current:
        # ① 跑当前节点（兼容 async 和普通函数）
        node_func = self.nodes[current]
        if inspect.iscoroutinefunction(node_func):
            state = await node_func(state)
        else:
            state = node_func(state)

        # ② 决定下一个节点去哪
        if current in self.conditions:
            route_fn = self.conditions[current]
            current = route_fn(state)          # 条件边优先
        else:
            old_current = current
            current = None
            for start, end in self.edges:      # 找第一条匹配的普通边
                if start == old_current:
                    current = end
                    break

    return state
```

三点值得说：

**① `inspect.iscoroutinefunction` 是为了省事。** 有些节点就是纯计算（比如拼字符串），写成同步函数更自然；引擎帮你看一眼是不是 `async def`，是就 `await`、不是就直接调。**让节点写法自由，引擎负责适配。**

**② 条件边优先于普通边。** 如果某个节点既注册了条件边又有普通边，走条件边。所以**别给同一个节点同时配两种边**。

**③ 找不到下一条边就结束。** `current = None` 退出循环——**没有显式终点声明**，走到没有出口的节点就是终点。

**这个 48 行的引擎没做的三件事**（知道边界比知道功能更重要）：

- **没有环检测**：图里如果连成一个环且没有条件边，`while` 会一直转下去。安全靠**使用者不连环**，不靠引擎。
- **没有并行**：一次只有一个 `current`。**并行在第 05 章用 `asyncio.gather` 实现**，在图的外面。
- **`interrupt_before` 是死代码**：声明了"暂停点"但从没被用过——`invoke` 里根本没读它。属于**当前版本留的坑位**，别被它误导。

> `entry_point` 也只能直接赋值（`graph.entry_point = "start"`），没有 `set_entry_point()` 方法。要更规整可以自己加。

### 2.4 一个最小可运行例子

```python
graph = AgentGraph()
graph.add_node("start", lambda s: {**s, "tag": "走了 start"})
graph.add_node("check", lambda s: {**s, "n": s["n"] + 1})
graph.add_node("big", lambda s: {**s, "route": "big"})
graph.add_node("small", lambda s: {**s, "route": "small"})

graph.add_edge("start", "check")
graph.add_condition_edges("check", lambda s: "big" if s["n"] > 5 else "small",
                          {"big": "big", "small": "small"})
graph.entry_point = "start"

await graph.invoke({"n": 10})     # → route = "big"
```

> 节点函数**返回新字典**（`{**s, ...}`）是个好习惯，但引擎并不强制——你原地改 `state["x"] = 1` 也能跑（因为传的是同一个对象引用）。**ReActAgent 就是原地改的**（`messages.append`），第 4 节会讲这个小坑。

---

## 三、ReActAgent：节点内部的循环

图管"节点之间"，那一个节点**内部**怎么"调工具 → 看结果 → 再决定"？

这就是 ReAct（Reasoning + Acting）。实现在 `backend/agents/data_analysis/simple_agent.py`，50 行的循环。

### 3.1 四步循环

```python
async def run(self, messages: list[Message]) -> str:
    tool_specs = self.registry.get_all_specs()

    for _ in range(self.max_rounds):                      # 上限 10 轮
        response = await self.llm.chat(messages, tools=tool_specs)

        if not response.tool_calls:                       # ② 没调工具 = 任务完成
            return response.content

        messages.append(Message(                          # ③ 记下 assistant 的调用请求
            role="assistant", content="", tool_calls=response.tool_calls,
        ))

        for tc in response.tool_calls:                     # ④ 执行 + 把结果回灌
            result = await self.registry.execute(tc.name, **tc.arguments)
            messages.append(Message(
                role="tool", content=_result_text(result), tool_call_id=tc.id,
            ))

    # ⑤ 超轮数，强制总结
    response = await self.llm.chat(messages)
    return response.content
```

| 步 | 干什么 | 关键词 |
|---|---|---|
| ① | 带上**全部工具说明书**问 LLM | `tools=tool_specs` |
| ② | LLM 不调工具 = 它认为查完了 | **终止条件** |
| ③ | 把 LLM 的调用请求存进历史 | `tool_calls` |
| ④ | 执行工具，结果存进历史 | `tool_call_id` |
| ⑤ | 到顶了强制它总结（注意：**这次不带 tools**） | 收场 |

**第 ⑤ 步那个细节很关键**：最后这次调用故意**不传 `tools`**——不传工具，模型就没有调用的可能，只能老老实实说话。相当于把"继续查"这个选项从它手里收走。

### 3.2 为什么 `tool_calls` 和 `tool_call_id` 必须成对

这是 ReAct 最容易踩的坑，和第 01 章 5.1 节是同一件事的两端：

```
assistant 消息：tool_calls = [{id: "call_abc", name: "query_sales", ...}]   ← 发出请求
tool 消息：     tool_call_id = "call_abc", content = "{...}"                ← 回应请求
```

**OpenAI 协议要求每个 `tool_calls` 里的 `id`，都必须有一条对应的 `tool` 消息回应**。少一条，下一次请求直接 **400**：

```
Invalid parameter: messages with role 'tool' must be a response to a preceding message with 'tool_calls'
```

而循环里一旦 400，整个任务就废了。所以第 01 章 `_to_qwen_messages` 里那两个 `if` 一个都不能少——**它们在为这里的循环兜底**。

> 也就是说：**ReAct 不是"LLM 自己循环"，而是"你把历史拼对了，LLM 才看得懂上一轮发生了什么"**。工具结果不是靠"注入"进 prompt 的，而是**当成一条消息追加进历史**（代码里那句注释写得很直白：*LLM 会自动从消息历史看到工具结果，不需要注入*）。

### 3.3 失败也要喂回去：`_result_text`

```python
def _result_text(result) -> str:
    """工具结果 → 喂给 LLM 的文本

    失败时必须带上 error：否则 LLM 只看到 data（None），
    不知道失败原因，下一轮没法自我纠正（参数传错也改不回来）。
    """
    if result.success:
        return str(result.data)
    return f"调用失败：{result.error}"
```

这段和第 02 章契约层的"**返回错误而不是抛异常**"是**一套设计的两半**：

```
第 02 章：工具层不抛异常 → 返回结构化错误
第 04 章：循环层把错误当正常消息喂回去 → LLM 下一轮自我纠正
```

第 02 章那个 `请补齐后重新调用` 的提示语，真正的**消费者就是这里**——没有这一环，契约层写得再细致，模型也看不到。

### 3.4 上限与收场

`max_rounds` 默认 **10**（`ReActAgent(llm, registry, max_rounds=10)`）。它是**安全阀**：模型有时会陷入"换个参数再查一次"的循环，没有上限就是烧钱。

到顶后不报错、不返回空，而是**强制总结**——把已有的信息组织成回答。宁可答得不全，也别给用户一个 500。

> 面试问"Agent 停不下来怎么办"，答案就是这三层：**轮数上限（这里）+ token/时间预算（第 05 章）+ 工具错误可自纠（3.3）**。

### 3.5 一个小坑：`run()` 会原地改你传进来的 list

```python
messages.append(...)     # 改的是调用方传进来的那个 list
```

`run(messages)` 里所有 `append` 都**作用在传入的列表对象上**。这意味着：跑完之后你再读这个 `messages`，它已经被追加了 assistant/tool 消息（好处是调用方能拿到完整轨迹；坏处是**同一份历史重复跑两次会越滚越长**）。

要隔离的话传副本：`await agent.run(list(messages))`。

---

## 四、两种跑法：`run` 与 `run_stream`

同一个循环写了两个版本：

| | `run()` | `run_stream()` |
|---|---|---|
| 返回 | 一个字符串 | `yield` 一系列事件字典 |
| 用途 | 后台分析、子任务 | 前端实时展示 |
| 中途 | 全程静默 | 每步推 `status`、每个工具推 `tool_result` |
| 收尾 | 直接返回文本 | 最后回复**流式**吐出 |

`run_stream` 推的事件类型：

```python
{"type": "status",      "content": "第2步：调用 check_stock..."}   # 进度提示
{"type": "tool_result", "tool": "check_stock", "data": "..."}      # 工具返回了什么
{"type": "token",       "content": "根"}                           # 最终回复逐字
{"type": "done"}                                                    # 结束
```

**为什么中间步骤不流式、只有最后回复流式？** 因为中间轮的输出要么是**工具调用**（结构化 JSON，流式拼装反而麻烦）、要么是没用完就丢的中间想法。**流式的价值在"给人看"**——只在真的要展示时开（第 06 章推 SSE 时会看到这一层怎么透传出去）。

> 一处**可改进的留白**：`run_stream` 最后那次 `chat_stream(messages, tools=tool_specs)` 仍然带了 `tools`。理论上模型可能在流式里又吐 `tool_calls`，而代码只读 `delta.content`、会把它丢掉。去掉这个 `tools=` 更严谨（和 `run()` 第 ⑤ 步不带 tools 一致）。

---

## 五、为什么自己写，而不用 LangGraph

这是面试高频题，答案要诚实：**因为我用过 LangGraph，才更清楚这里只需要什么。**

| | LangGraph | 本项目的 AgentGraph |
|---|---|---|
| 代码量 | 一整个库 | 48 行 |
| 能力 | 检查点、持久化、人工介入、子图、并行… | 节点 / 边 / 条件边 / 跑 |
| 适合 | 复杂状态机、要断点续跑 | **线性 + 一层分支**，够用 |

真实理由有三条：

1. **需求只有"一层分支"**：intent 分四路、每路往后跑到底——不需要子图、不需要检查点；
2. **调试要看得见**：48 行全部能读懂，出问题能一行行查（用框架时出问题常常变成"猜框架"）；
3. **面试讲得深**：能讲清 `iscoroutinefunction` 为什么在这、条件边为什么要绕一层 mapping——**比"我用了 LangGraph"信息量大得多**。

> 但别把话说死。诚实的版本是：**"当前规模自研够用，如果要做断点续跑 / 人工审批 / 复杂嵌套，我会换 LangGraph 或把这里补齐。"** 这也是第 13 章缺陷清单里的备选项。

---

## 六、验证一下

**① 单独验引擎**（不需要 LLM、不花钱）：

```python
from backend.core.agent.base import AgentGraph

g = AgentGraph()
g.add_node("a", lambda s: {**s, "log": s.get("log", []) + ["a"]})
g.add_node("b", lambda s: {**s, "log": s["log"] + ["b"]})
g.add_edge("a", "b")
g.entry_point = "a"

print((await g.invoke({}))["log"])    # → ['a', 'b']
```

把 `add_edge` 换成 `add_condition_edges`，验证岔道能按 state 走对路。

**② 验 ReAct 循环**（用假 LLM，看历史拼对没有）：

```python
class ScriptedLLM(BaseLLM):     # 第一轮返回 tool_calls，第二轮返回文本
    ...
```

跑完打印 `messages`，确认**每一条 `tool` 消息的 `tool_call_id` 都能在上一轮 `tool_calls` 里找到**——这是本节最该验的东西。

项目里已经有两个现成脚本可参考：

```bash
PYTHONPATH=. python scripts/verify_tool_args.py         # 故意喂坏参数，看模型能不能自纠
PYTHONPATH=. python scripts/cli.py                       # 命令行直接跟 Agent 对话
```

---

## 本章小结

- **AgentGraph（48 行）**：state 是字典、node 是函数、edge 管跳转、condition 管分支；**没有环检测、没有并行、`interrupt_before` 是死代码**
- **ReAct 四步**：问 LLM → 有 tool_calls 就执行 → 记 assistant + tool 两条消息 → 循环；**没有 tool_calls 就是终止**
- **`tool_calls` 与 `tool_call_id` 必须成对**，否则 API 400 —— ReAct 靠"历史拼得对"而不是"注入 prompt"
- **失败要喂回 error**，让模型自我纠正；轮数上限兜底，超了**强制总结**（且不带 tools）
- **自己写不是造轮子**：需求只有一层分支，48 行的可调试性比框架的能力更值钱

---

## 练手挑战

**挑战一（必做）：给 AgentGraph 加环检测**

在 `invoke` 里记录走过的节点序列，同一个节点在**没有条件边**的情况下被走第二次就抛异常。验收：连一条 `a → a` 的边，跑起来能报出可读错误而不是卡死。

**挑战二（推荐）：把 `max_rounds` 的上限行为测出来**

写个假 LLM，让它**每轮都返回 tool_calls**（永远不结束）。确认第 10 轮后走的是"强制总结"分支，而不是抛异常或返回 `None`。

**挑战三（加分）：修掉 4 节那个留白**

把 `run_stream` 最后那次 `chat_stream` 的 `tools=tool_specs` 去掉，说明为什么这样更严谨。

---

## 下一步

引擎会转圈了，但它一次只跑一个循环。真实问题（"这个月为什么跌了"）要拆成好几个子任务、还得并行跑——第 05 章看**多 Agent 编排**：supervisor 怎么路由、planner 怎么拆活、executor 怎么并行。

→ [05 多 Agent 编排](05-多Agent编排.md)
