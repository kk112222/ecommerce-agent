# 05 · 多 Agent 编排

> 第 04 章的引擎能转圈了，但它一次只跑一个循环。
> 真实问题（"这个月销量为什么跌"）得拆成好几件事、还得**并行**查。
> 这一章把图铺开：**路由 → 拆解 → 并行执行 → 汇总**，四个环节各有一个 Agent 负责。

---

## 本章目标

读完这一章，你会明白：

1. 为什么"一个 ReActAgent 包打天下"不够用
2. supervisor 这张图怎么组装（8 节点 / 2 条普通边 / 2 组条件边）
3. 意图路由为什么**必须是同步函数**
4. planner 怎么把目标拆成**能并行**的子任务，以及怎么校准 LLM 的脏输出
5. 一个子任务炸了，怎么**不拖垮整轮**（三层异常隔离）
6. 技能层：怎么把"这件事该按什么口径做"变成可热更新的外部文件
7. 技能声明的**落盘**为什么是独立后置节点，而不是"给 executor 多开一个工具范围"

---

## 一、先看痛点：一个 Agent 包打天下会怎样

最早这个项目就是**一个 `ReActAgent` 加 10 个工具**。跑起来能用，但三个问题很快暴露：

| 问题 | 现象 |
|---|---|
| **工具太多，选不准** | 让模型同时看着"查销售 + 写标题 + 查知识库 + 写文件"10 个工具，它经常选错 |
| **没法并行** | 一个 while 循环只能顺序调工具，"查销售"和"查库存"要排队 |
| **一张 prompt 装不下所有角色** | 分析师、文案、客服的性格和要求完全不同，混一段 system prompt 里互相打架 |

所以拆成**多个角色 Agent + 一层调度**：

```
                 谁来决定派给谁  →  supervisor（调度）
   每个角色自己怎么想、怎么写  →  角色 Agent（分析师 / 文案 / 客服 / 文档）
   每个角色能碰哪些工具        →  角色级白名单（第 02 章）
```

> **重点**：这里的"多 Agent"不是"多个模型互相聊天"。**底下只有一个 LLM**，多出来的是**不同的 prompt 角色 + 不同的工具白名单 + 不同的执行形态**。这条要在面试里说清楚，不然容易被追问"你们用了几个模型"。

---

## 二、整体拓扑

`backend/agents/supervisor.py` 的 `build_supervisor()` 把整张图拼出来：

```
                    ┌──────────┐
   用户目标 ───────▶│  intent  │  意图分类（LLM，temperature=0）
                    └────┬─────┘
                         │ 条件边 intent_router(state)
        ┌────────────────┼────────────────┬────────────────┐
        │ analysis       │ content        │ service        │ document
        ▼                ▼                ▼                ▼
   ┌─────────┐      ┌─────────┐      ┌─────────┐      ┌─────────┐
   │ planner │      │ content │      │ service │      │document │
   └────┬────┘      └────┬────┘      └────┬────┘      └────┬────┘
        ▼                │                │                │
   ┌─────────┐           │   ReAct        │   ReAct        │   ReAct
   │executor │ 并行×N    │   单轮         │   单轮         │   单轮
   └────┬────┘           │                │                │
        ▼                ▼                ▼                ▼
   ┌──────────┐        report           report           report
   │synthesize│
   └────┬─────┘
        │ 条件边 save_router(state)
        │
        ├── 技能声明了 save ──▶ ┌──────┐
        │                       │ save │  把报告写进文件（可选后置）
        │                       └──────┘
        │
        └── 否则 ──────────────▶ 结束（report 已经流式推给前端了）
```

链路和节点，一张表说清（前两行是**链路**，第三行是**节点**，别混）：

| 链路 / 节点 | 形态 | 为什么这么设计 |
|---|---|---|
| **analysis** | planner → executor×N（并行）→ synthesize | 一个问题要**多个方向**查，查完还要**综合** |
| **content / service / document** | 单个角色 Agent 跑一次 ReAct | 一件事一次做完：写文案、查政策、生成文档 |
| **save**（不是链路，是后置节点） | 挂在 synthesize 下游，可选 | 只有技能声明了落盘才走，见 6.4 |

组装代码全在这里：

```python
# 分析链路：串行
graph.add_edge("planner", "executor")
graph.add_edge("executor", "synthesize")

# 落盘是 synthesize 的**可选后置**：技能声明了 save 才走，没声明就到 None=结束
def save_router(state):
    skill = skills_by_name.get(state.get("skill") or "")
    return "save" if (skill and skill.save) else "end"
graph.add_condition_edges("synthesize", save_router, {
    "save": "save",
    "end": None,          # None = 终止（invoke 的 while current: 遇假值即退出）
})

# 条件边：intent 跑完后，根据 state["intent"] 选链路（router 是同步函数）
def intent_router(state):
    return state.get("intent", "analysis")
graph.add_condition_edges("intent", intent_router, {
    "analysis": "planner", "content": "content",
    "service": "service", "document": "document",
})

graph.entry_point = "intent"
```

> 复习一下第 04 章：**普通边控制"一路往下"，条件边控制"分岔"**。整个多 Agent 编排，本质上就是**两条分岔 + 一条串行链**——
> 一条分岔选链路（intent），一条分岔决定要不要落盘（synthesize），没有更复杂的东西。

---

## 三、意图路由：第一站永远是分类

```python
async def intent_node(state):
    # 先判断意图存进 state["intent"]，后面条件边的 router 只读它
    # （router 必须是同步函数，不能在里面 await LLM）
    try:
        state["intent"] = await classifier.classify(state["goal"])
    except BudgetExceeded:
        state["intent"] = "analysis"     # 开局就没额度：按最通用的链路走，后面逐级降级
    if on_event:
        await on_event({"type": "intent", "intent": state["intent"]})
    return state
```

**为什么要拆成"节点里 await" + "router 里只读"？** 这是第 04 章那个 48 行引擎的硬约束：

```python
if current in self.conditions:
    current = route_fn(state)      # ← 这里是同步调用，没有 await
```

`invoke` 里调 `route_fn(state)` 时**没有 `await`**。如果 router 写成 `async def` 并在里面 `await llm.chat(...)`，它返回的是一个**协程对象**，`mapping[协程对象]` 直接 `KeyError`（而且协程从未被 await，还会报警告）。

> 这条约束值得记住：**引擎简单 → 使用者的自由度就小**。第 04 章说"自研 48 行的代价是没有环检测"，这里是第二个代价：**分支判断不能是异步的，只能靠前面的节点把结论写进 state**。

### 分类器怎么写才稳

`backend/agents/intent_classifier.py` 做了三件兜底：

```python
temperature=0                                   # 分类任务不要随机性
text = re.sub(r'^```(?:json)?|```', '', ...)    # 剥掉 ```json 围栏（LLM 的老毛病）
if intent not in INTENTS:
    intent = "analysis"                         # 解析失败 / 编造类别 → 默认走分析链路
```

最后那句**默认值的选择**很关键：**analysis 是能力最强的链路**（会规划、会并行、会综合），兜底到它最不容易"答不出"。反之如果默认成 content，用户问数据就会被答成一段文案。

> 代码里还有个小注释值得抄下来：`Message(role="user", content=goal)  # user 消息必须放目标（踩过的坑！）`—— 把目标塞在 system 里、user 位置留空，分类准确率会掉。**分类任务里"要判断的东西"必须是被判断的那条消息。**

---

## 四、planner：把目标拆成"能并行"的子任务

`backend/agents/planner.py` 是整条链路里最有技术含量的一段。

### 4.1 五条硬规则

prompt 里最值钱的是这几条（都是踩出来的）：

| 规则 | 为什么 |
|---|---|
| 拆 **2~5** 条 | 太少 = 没真正拆解；太多 = 每条的上下文都变薄、成本和延迟飙升 |
| 每条**独立完成、不依赖别的子任务输出** | **这条是整个并行设计的地基**——有依赖就只能串行，`asyncio.gather` 直接失效 |
| 按**"要回答的问题"**拆，不按"工具"拆 | 一条子任务内部可以调多个工具交叉验证（查销售 → 发现跌 → 再查库存） |
| `tool_hint` 只是**首选工具提示**，不是权限边界 | 第 02 章的教训：按单工具硬锁会**静默给出残缺结论** |
| 只输出 JSON | 后面要 `json.loads` |

> 第 3 条和第 4 条是一体两面：**如果按工具拆，就必然要按工具锁权限**。放弃"按工具拆"，才换来"同角色内自由组合工具"。

### 4.2 `_sanitize`：把 LLM 的脏计划校准成"下游一定能用"的形状

LLM 输出的计划**永远不能直接信**。它会给：不是 list、缺 `task`、`id` 重复或缺失、`tool_hint` 编造工具名。这几样在旧版本都出过事：

| 脏输入 | 后果 |
|---|---|
| `id` 重复 | 下游 `{t["id"]: r for t, r in zip(plan, results)}` 把两条结果**折叠成一条**——静默丢结果 |
| 缺 `task` | 直接 `KeyError` |
| 编造的工具名 | 模型看到一句无意义的提示 |

`_sanitize` 逐条补齐 + 去重 + 校验。**里面那段 `id` 去重值得单独看**：

```python
if tid in seen_ids:                      # id 重复 → 重编，避免结果被折叠
    # 候选名必须每次循环都往前推：seen_ids 只在循环外 add，写成
    # `while tid in seen_ids: tid = f"t{len(seen_ids) + 1}"` 的话 len() 是常量，
    # 候选名固定不变 → 一旦被占就原地自旋，同步 CPU 跑在请求路径上会堵死整个事件循环
    n = len(seen_ids) + 1
    while f"t{n}" in seen_ids:
        n += 1
    tid = f"t{n}"
```

这是本文件里**最像"真踩过"的一段**：`while tid in seen_ids: tid = f"t{len(seen_ids)+1}"` 看起来天经地义，但 `len(seen_ids)` 在循环体里**不变**（新的 id 是在循环之后才 add 的），所以候选名永远是同一个 → 一旦被占就**原地死循环**。而且这段跑在**请求路径的同步代码里**，会把整个 asyncio 事件循环堵死——服务直接不响应。

> 教训：**`while` 循环体里必须让"判断条件依赖的变量"真的变化**。这类 bug 在异步服务里代价特别大。

### 4.3 `_replan_with_skill`：技能命中的第二步

第一轮规划时，模型只看到技能的**元数据**（名字、适用场景几行字）。它判断"这次目标对得上某个技能"，就在 `skill` 字段填上技能名。然后**再规划一次**，这次把技能**正文**（口径、步骤）也带上：

```python
refined = await self._replan_with_skill(goal, skill, ...)
if refined:
    return {"skill": skill.name, "tasks": refined}
# 正文白展开了：这里当"没命中"处理（连工具范围也不放宽）
logger.warning("技能 %s 加载了但没能规划出可用计划，本次按普通规划走（技能不计入命中）", skill.name)
return {"skill": "", "tasks": tasks}
```

**为什么"二次规划失败"要退回"没命中"，而不是"算命中、用第一轮计划"？** 注释说得很清楚：

> 否则会出现"**因为技能放开了写文件的权限，任务却是现编的**"——权限跟着技能走，而技能实际没生效，两边对不上。

这是本章**最该学的一个设计习惯**：**当一个标记同时控制"能力"和"内容"时，要么两者都生效，要么都不生效**，不能只用一半。

---

## 五、executor：并行 + 异常隔离

### 5.1 并行

```python
results_list = await asyncio.gather(*[run_one(t) for t in plan], return_exceptions=True)
state["results"] = {
    t["id"]: (r if not isinstance(r, BaseException) else _fail_note(r))
    for t, r in zip(plan, results_list)
}
```

**并行能成立，全靠 4.1 节那条"子任务互相独立"的规则**——如果 `t2` 要用 `t1` 的结论，这里就只能改成串行。

`asyncio.gather` 让 N 个子任务同时跑，但**每个子任务内部是完整的 ReAct 循环**（第 04 章那个），里面还在调 LLM、查数据库。所以这里的**真正瓶颈是 LLM 的并发额度**——这也是第 01 章 5.3 节非要把 `requests` 丢进 `asyncio.to_thread` 的原因：**如果 LLM 调用是同步阻塞的，`gather` 出来的"并行"会退化成排队**。

### 5.2 三层异常隔离

`run_one` 把每个子任务包了三层：

```python
async def run_one(t):
    try:
        r = await executor.run(t, uploaded_data=..., user_profile=..., scopes=scopes)
    except BudgetExceeded as e:
        r = _budget_note(e)          # ① 预算用尽 → 降级文案
    except Exception as e:
        logger.exception("子任务执行失败：%s", t.get("id"))
        r = _fail_note(e)            # ② 其它异常 → 降级文案 + 日志
    if on_event:
        await on_event({...})
    return r
```

```python
results_list = await asyncio.gather(..., return_exceptions=True)   # ③ 连 on_event 抛的也接住
```

三层各挡一种情况：

| 层 | 挡什么 | 挡不住的后果 |
|---|---|---|
| ① `BudgetExceeded` | 某个子任务把额度吃光了 | 另外三个**已查好的数据全被丢掉** |
| ② `Exception` | LLM 500 / 超时 / 工具内部报错 | 异常穿过 `gather` 冒到 `AgentGraph.invoke`，**图直接中断、`synthesize` 永不执行** |
| ③ `return_exceptions=True` | 连降级代码自己都抛了（比如 `on_event` 推 SSE 失败） | 前两层白做 |

**核心思路一句话：一格失败 ≠ 整轮失败。** 报告结构保持完整（那一格变成一句占位文案），`synthesizer` 照样能把跑完的三个子任务综合出来。

> 代码里的注释很诚实地写下了改之前的症状：*"另外三个子任务已经查好的数据全被丢掉，用户看到的是'分析失败'"*。

### 5.3 降级不等于免责

注意 ② 里那句 `logger.exception(...)`。注释写得很到位：**"降级不等于免责：必须留日志，否则'偶发 500'只剩报告里一句软话，没人查得到。"**

这句话和第 02 章契约层的"**静默降级 = 没人查得到，所以一定要留日志**"是同一条原则的第二次出现——**该优雅降级的地方都降级，但每一次降级都要留下痕迹**。

### 5.4 子任务自己长什么样

`executor.run` 干三件事：

```python
# ① 拼"子任务专属" prompt（今天是几号 / 你的子任务 / 工作方式 / 200 字以内输出）
# ② 复用 ReActAgent 跑独立循环（第 04 章的引擎）
registry = self.registry.subset_scopes(scopes or ["analysis"])
agent = ReActAgent(self.llm, registry)
result = await agent.run([Message(role="system", content=system_prompt)])

# ③ 空结果兜底
if not result or not result.strip():
    return "该子任务未获取到数据：工具查询无结果或执行器未返回内容。"
```

两个细节：

- **`今天是 {today}` 要写进 prompt**：用户说"本周"，模型得知道今天是几号才能算出日期范围。**LLM 没有时钟**，这种信息必须显式喂。
- **`hint` 从"指定工具"改成"建议优先使用"**：文案上就体现了第 02 章那次边界调整——提示语写"必须用 X"，模型会以为没别的选择。

> 一处**可以对齐的留白**：`executor` 和 `synthesizer` 都只发了一条 `system` 消息、没有 `user` 消息；而 `intent_classifier` 里专门留了注释说 *"user 消息必须放目标（踩过的坑！）"*。三处写法不一致，值得统一验一下哪种更稳。

---

## 六、技能层：把"口径"从 prompt 里搬出来

这是最近新加的一层，也是这个项目目前**最有"产品感"的设计**。

### 6.1 要解决的问题

运营团队有自己的**固定口径**：周报要环比、要看几个固定维度、要跟上一周期对比。这些知识如果全写进 planner 的 prompt：

- 越写越长（prompt 是成本，也是干扰）；
- 改一次口径就要改代码 + 重启服务。

### 6.2 做法：渐进披露（progressive disclosure）

技能就是 `backend/skills/*.md` 文件，带 front-matter：

```markdown
---
name: weekly-report
title: 经营周报
when: 用户要"周报 / 月报 / 复盘 / 总结这一周（月）的经营情况"，或要求按固定口径汇总一段时间的整体表现
scopes: analysis
save: 经营周报-{date}.md
---

# 经营周报
（正文：要看的维度、输出要求…）
```

加载分成**两步**：

```
第一步（常驻）：只把 名字 + title + when 几行元数据 放进 planner 的 prompt
                 → 模型据此判断"这次像不像周报"

第二步（命中才加载）：真的像了，再把**正文**拉进 context 重新规划一次
                 → 没命中就一个字都不加载
```

这就是**渐进披露**：**常驻的只有索引，内容按需加载**。和 RAG 是同一个思路，区别是 RAG 检索的是文档片段、这里检索的是"流程知识"。**改口径只要改 md 文件，不重启服务**（`load_skills()` 每次调用都重新读盘）。

### 6.3 `scopes`：技能还能决定工具范围

```python
skill = skills_by_name.get(state.get("skill") or "")
scopes = skill.scopes if skill else None      # None → executor 用默认的 analysis 角色范围
```

**为什么工具范围要跟着技能走？** planner 的注释给了理由：

> 技能横跨角色时（周报既要查数据、又要把结果落盘成文档），光按"请求走的是分析链路"给范围是给不全的。

也就是说：**技能名是"这次用的是哪套流程"的唯一凭据**。请求走的是 analysis 链路，但命中的技能可能还要写文件——不传 `scopes`，"周报存成文档"这一步就永远做不到。

安全性没变：**模型能选的只有"用哪个技能"，选不了"要多大权限"**。范围来自代码里 `TOOL_SCOPES` 的白名单，技能只能声明范围**名字**，不能自己发明权限。

### 6.4 `save`：技能声明的后置落盘

技能除了 `scopes`，还能声明一个 `save:` —— **报告产出之后存成什么文件**（文件名可以带 `{date}` 占位）。真正干这件事的是图里第 8 个节点 `save`。

**为什么是独立节点，而不是"给 executor 放开 document 范围"？** `save_node` 的注释一句话说穿了：

> executor 跑在 synthesize **之前**，`state["report"]` 那时还没诞生 —— 它手里根本没有要落盘的东西。

这句话值得单独记：**工具范围（`scopes`）解决的是"谁有权调"，解决不了"东西还没生成"。** 这是两个不同的问题——

| 问题 | 用什么解 |
|---|---|
| 这个角色**能不能**调某个工具 | `scopes` 白名单（6.3） |
| 这一步**有没有东西可调** | **节点在图里的位置**（6.4） |

落盘对象在报告之后才存在，所以这一步只能挂在 `synthesize` 下游，不能塞进 `executor`。

两条边界，都是踩出来的：

| 边界 | 不这么做的后果 |
|---|---|
| **降级报告不落盘** | `synthesize` 走兜底时 state 带 `report_degraded` 标记，`save` 直接返回。**残件混进交付物**，而看文件名完全看不出来 |
| **落到非用户目录要告警** | 工具的 `user_id`/`session_id` 来自注册时的 context，缺了就落到 `outputs/` 根目录；而下载/列表都以 `outputs/user_<id>/` 为根 → 文件确实在，但**谁都拿不到** |

还有个小设计：**落盘通知不用新造事件**。`write_document` 的 `on_document` 回调会把 `document` 事件塞进 SSE 队列（`chat.py` 把 `queue.put_nowait` 注进了工具 context），前端已有的 `document` 分支会自动给出下载入口 + 时间线步骤——**第 09 章那套"事件流驱动时间线"在这里白捡一个功能**。

> 现状说明（别被文档带跑）：目前 `backend/skills/` 里**只有 `weekly-report.md` 一个技能**，声明 `scopes: analysis` + `save: 经营周报-{date}.md`。
> 注意"跨角色"这件事最后**没有走"给 executor 放开范围"这条路**，而是用独立后置节点解决的（就是上面这个理由）。
> `scopes` 是列表、`subset_scopes` 也支持并集，这条路径的能力留着，但目前仍**没有实际用例在跑**。

---

## 七、synthesizer：从 N 份调查到一份报告

最后一步是**综合**——这一步不能省。原因：子任务各自输出的是"我查到了什么"，**彼此不知道对方发现了什么**。只有让一个角色看到**全部子任务结果**，才能做交叉印证（"销量跌了 + 库存也低 + 会员流失"合起来才是结论）。

```python
async def synthesize_node(state):
    full = ""
    try:
        async for chunk in synthesizer.synthesize_stream(
                state["goal"], state["results"], history=..., user_profile=..., uploaded_data=...):
            full += chunk
            if on_event:
                await on_event({"type": "token", "content": chunk})   # 边生成边推给前端
    except BudgetExceeded as e:
        full = f"{full}\n\n{_budget_note(e)}" if full else _fallback_report(state)
    state["report"] = full
```

prompt 里的输出要求有两条特别值得记：

1. **"先正面回答原始目标"**——综合报告最容易犯的毛病是变成"子任务结果大拼盘"，读完了还不知道结论是啥。所以要显式要求**先给答案**；
2. **`VIZ_RULES`：禁止自己算数字**。

```python
"""数值必须【逐字照抄】上方【各子任务调查结果】里出现过的数字，
禁止自行计算/估算/编造；抄不准或没把握就不加 viz 块，宁可纯文字"""
```

这条是**针对 LLM 编数据**的硬约束。报告末尾可以附一个 ```viz 代码块（内嵌图表 JSON），前端识别后渲染成图表——**但数字必须是子任务结果里出现过的原文**。宁可不出图，也不能出个漂亮的错图。**"没把握就退化成纯文字"这个授权非常重要**——不给模型这个退路，它就会为了满足格式而编数。

---

## 八、降级总表（一整轮请求可能在哪几处降级）

这张表建议背下来，面试问"你的系统怎么做容错"直接照着讲：

| 位置 | 触发 | 怎么降 |
|---|---|---|
| `intent_node` | 预算在分类前就耗尽 | 按最通用的 `analysis` 链路走 |
| `intent_classifier` | JSON 解析失败 / 编造类别 | 默认 `analysis` |
| `planner.plan` | 预算耗尽 | `plan = []`，**不猜任务**，直接走"无结果"降级报告 |
| `planner._sanitize` | 计划形状不合法 | 兜底成"把用户目标当一个子任务" |
| `planner._pick_skill` | 编造技能名 | 忽略 + 告警 |
| `planner._replan_with_skill` | 二次规划不可用 | **当没命中**，退普通规划 |
| `planner`（tool_hint） | 非法工具名 / 填了多个 | 清空 + 告警（只影响提示质量，不削能力） |
| `executor.run` | 工具查不到数据 / 返回空串 | 返回"未获取到数据"占位 |
| `executor_node` | 单个子任务预算耗尽 | 该格降级文案，**其它格照跑** |
| `executor_node` | 单个子任务其它异常 | 该格降级文案 + **日志** |
| `executor_node` | 连降级代码都抛 | `return_exceptions=True` 兜住 |
| `synthesize_node` | 综合时预算耗尽 | 已流式的部分保留；一个字没有就用子任务结果**拼兜底报告** |
| `synthesize`（出图） | 数字没把握 | 不加 viz 块，纯文字 |
| `save_node` | 报告是降级产出 | **不落盘**（残件不混进交付物） |
| `save_node` | 落盘失败 | 只留警告日志，**不连累已推出去的报告** |
| `save_node` | 落到了非用户目录 | 警告留痕（文件在，但列表/下载都看不见） |

**贯穿全表的一条原则**：**降级要"少给"，不要"崩"**。用户拿到一份不完整的报告，永远好过一个 500。

> 也正因为降级点这么多，**降级必须留日志**这件事在本层里就出现了四次（工具契约层、子任务隔离、技能退回、报告落盘）。这是一条真正被反复强调的原则，不是写一次就完的话术。

---

## 九、验证一下

**① 单独验路由**（用假 LLM 强制返回四类 intent，确认四条链路都能走到）：

```python
# 断言：content 意图下，state 里不应有 "plan" / "results"；
#       analysis 意图下，应依次有 plan → results → report
```

**② 单独验并行**（在 `run_one` 里打时间戳）：

```python
# 3 个子任务若并行，总耗时 ≈ 最慢的那个，而不是三个之和
```

**③ 验异常隔离**（最有价值的一项）：让 `executor.run` 对 `t2` 抛异常，确认：

- 报告仍然生成；
- `t2` 那格是 `（该子任务失败：...）`；
- 日志里有完整栈。

项目里的现成材料：

```bash
PYTHONPATH=. python scripts/verify_tool_args.py       # 参数纠错链路
ls tests/                                             # 151 项离线用例（假 LLM，不联网）
```

---

## 本章小结

- **多 Agent ≠ 多模型**：一个 LLM + 多个 prompt 角色 + 多份工具白名单 + 不同执行形态
- 拓扑只有两种链路：**analysis 链路（拆解→并行→综合）** 和 **单角色链路（跑一次 ReAct）**，外加 `synthesize` 之后一个**可选后置节点** `save`
- **条件边的 router 必须是同步函数**（`invoke` 里没有 `await`）→ 判断结论必须由前一个节点写进 state
- planner 的地基规则是"**子任务互相独立**"——**没有它就没有并行**
- **`_sanitize`** 校准脏计划；`while` 里那个自旋 bug 是"异步服务里最贵的 bug"
- **三层异常隔离**：预算 / 其它异常 / 连降级也抛，**一格失败不拖垮整轮**，但**降级必须留日志**
- **技能层**：元数据常驻、正文按需加载（渐进披露）；**技能同时决定工具范围**——所以"命中失败"必须退回"没命中"，不能只生效一半
- **权限问题和时序问题是两码事**：`scopes` 管"谁有权调"，**节点位置**管"东西生成了没"——所以落盘是 `synthesize` 的后置节点，不是给 executor 加权限（6.4）
- **降级贯穿全链路**：降级要"少给"，不要"崩"

---

## 练手挑战

**挑战一（必做）：把三个角色节点抽成公共函数**

`content_node` / `service_node` / `document_node` 是同一段代码抄了三遍（差别只在 `document` 多传了 `uploaded_data`）。写一个 `make_role_node(agent, extra=None)` 工厂，把三处替换掉。

**验收**：四条链路行为不变，`tests/` 全绿。

**挑战二（推荐）：加第五类意图**

比如 `chitchat`（寒暄，直接回一句话、不调工具）。需要改三处：`INTENTS`、分类器 prompt、条件边 mapping。想想为什么**条件边的 mapping 必须同步更新**——不加会怎样？

**挑战三（加分）：给技能层加第二个技能**

写一个 `monthly-rank.md`（月度商品排行，`scopes: analysis`），确认 planner 能在两个技能之间选对；再故意把 `scopes` 写成一个不存在的范围名，看第 02 章的降级规则（放开全集 + 告警）是不是真的生效。

**验收**：一份问题命中它、一份问题落回普通规划；`scopes` 写错时日志里有告警，且**没有崩**。

**挑战四（加分）：亲手验一次落盘的两条边界**

给上面那个新技能加一行 `save: 月度排行-{date}.md`，问一个能命中它的问题，确认：报告末尾出现下载入口、`outputs/user_<id>/` 下真有文件。

然后把 `AGENT_BUDGET_SECONDS` 调到极小（比如 `1`）再问一次 —— 让 `synthesize` 走预算兜底，**这次不应该落盘**。

**验收**：第一次有文件、第二次没有，且日志里有"报告是降级产出，本次不落盘"。这两次对比就是 6.4 那条边界存在的意义。

---

## 下一步

编排层跑通了，四条链路各司其职。但还有两块拼图没讲：

- **客服链路凭什么答得出"退货要多久"**——知识库怎么建、怎么查（第 06 章 **混合检索 RAG**）；
- **为什么它好像"记得你"**——三层记忆怎么落库、怎么回灌（第 07 章 **记忆系统**）。

→ [06 混合检索 RAG](06-混合检索RAG.md)
