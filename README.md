# 掌柜 · 电商运营 AI Agent

> 一个给电商运营人员用的 AI 助手：用自然语言提问，Agent 自动规划、并行调用工具查数、生成文案、检索知识库。

不是一问一答的聊天机器人，而是能**自主拆解任务、多步执行、汇总成稿**的 Agent。
从状态图引擎到 ReAct 循环、混合检索、三层记忆，**核心部件全部手写**，没有用 LangChain / LangGraph 这类编排框架。

---

## 它能做什么

运营人员直接在聊天框里说：

```
7月15号到今天，充电宝卖了多少？
帮我生成苹果15的三条淘宝标题
退货要多久，运费谁出？
把这份周报整理成 Word 发我
```

Agent 的完整链路：

1. **意图路由** —— 判断这是数据分析 / 内容生成 / 售后问答 / 文档处理
2. **规划拆解** —— 数据分析类先拆成 2~5 个互相独立的子任务
3. **并行执行** —— 每个子任务起一个 ReAct 循环，各自调工具、看结果、再决定
4. **流式汇总** —— 边生成边推送，前端实时显示执行时间线

---

## 核心特性

| 特性 | 说明 |
|---|---|
| **自研状态图引擎** | 48 行的 `AgentGraph`：节点 + 条件边路由，支持同步/异步节点 |
| **多 Agent 编排** | supervisor 按意图路由到 4 条链路，子任务 `asyncio.gather` 并行 |
| **ReAct 循环** | Function Calling 驱动的「想 → 调 → 看 → 再想」，带单轮预算熔断 |
| **技能层** | 流程知识渐进披露：元数据常驻，正文命中才加载，技能决定工具范围 |
| **混合检索 RAG** | 结构化切块 + BM25/向量双路召回 + RRF 融合 + Cross-Encoder 重排 |
| **三层记忆** | 会话历史 / 语义画像（增量合并）/ 情景记忆（TTL），召回相似度×重要性×新鲜度加权 |
| **SSE 流式** | 生产者-消费者分离，前端按事件驱动执行时间线，支持图表渲染 |
| **多租户隔离** | JWT + bcrypt，SQLite 按 `user_id` 过滤、Qdrant 用 Filter、文件按目录隔离 |

---

## 架构

```
用户（运营人员）
    │  自然语言提问
    ▼
[前端] React + TS + antd —— 聊天流 · 执行时间线 · 图表 · 多会话
    │  HTTP / SSE
    ▼
[API 层] FastAPI —— JWT 认证 · SSE 流式推送 · 请求日志/异常兜底
    │
    ▼
[编排层] supervisor —— 自研 AgentGraph 状态图（intent → 条件边路由）
    │
    ├─ analysis : planner → executor×N（并行 ReAct）→ synthesizer
    ├─ content  : 内容 Agent（ReAct：标题 / 文案）
    ├─ service  : 客服 Agent（ReAct：查知识库）
    └─ document : 文档 Agent（ReAct：解析 / 生成落盘）
    │
    ▼
[工具层] 10 个工具 ──→ SQLite · Qdrant · LLM
    │
    ▼
[LLM 层] 通义千问（BaseLLM 抽象 + 预算装饰器，可切换）
```

**三层依赖**是这个项目的骨架：

```
Tool 层   ← Function Calling 的"手" —— 执行具体操作
Agent 层  ← 编排 + 决策的"大脑" —— 决定派给谁、调什么、调几次
LLM 层    ← 思考的"发动机" —— 提供智能
```

---

## 快速开始

**环境要求**：Python ≥ 3.13、[uv](https://docs.astral.sh/uv/)、Node.js ≥ 18。
向量库用 Qdrant 本地文件模式，**不需要额外起服务**。

```bash
# 1. 安装后端依赖
uv sync

# 2. 配置环境变量：复制模板后填入真实值
cp .env.example .env
#   必填：DASHSCOPE_API_KEY（通义千问 API Key）、JWT_SECRET_KEY（随机字符串）

# 3. 初始化数据库 + 灌入模拟数据
uv run python -m scripts.init_db
uv run python -m scripts.seed_data

# 4. 构建知识库（解析 docs → 切块 → 向量化 → 入库）
uv run python -m scripts.build_kb

# 5. 启动后端（http://localhost:8000，自带 Swagger）
uv run uvicorn backend.api.app:app --reload --port 8000

# 6. 启动前端（另开一个终端）
cd frontend && npm install && npm run dev   # http://localhost:3000
```

> 脚本一律用 `python -m scripts.xxx` 而不是 `python scripts/xxx.py`：
> 后者会把 `scripts/` 而不是项目根目录放进 `sys.path`，`import backend` 会直接报
> `ModuleNotFoundError`。用 `-m` 或以 `PYTHONPATH=. python scripts/xxx.py` 运行都可以。

打开 http://localhost:3000，注册一个账号即可开始对话。

---

## 项目结构

```
backend/
  core/            # 基础设施：配置、LLM 抽象、AgentGraph、记忆存储、安全
  agents/          # supervisor 编排 + 意图分类 + planner/executor/synthesizer
  tools/           # 10 个工具，按 data / content / service / document 四类分组
  skills/          # 技能文档（Markdown + front matter），渐进披露加载
  infrastructure/  # 向量库、文档解析、混合检索
  api/             # FastAPI 路由、依赖注入、中间件
  db/              # SQLAlchemy 异步模型
frontend/          # React + TypeScript + antd 聊天界面
scripts/           # 初始化、灌数据、建库、评测、冒烟脚本
tests/             # 136 个 pytest 用例
docs/              # 教程式项目文档（见下）
```

---

## 测试与评测

```bash
uv run pytest                          # 136 个用例
uv run python -m scripts.rag_eval      # 检索层评测：Recall@5 / Hit@1 / MRR
```

`scripts/` 下另有 `agent_eval.py`、`rag_quality_eval.py`、`rag_answer_eval.py`
以及一组 `smoke_*` / `verify_*` 冒烟与验证脚本。

---

## 文档

`docs/` 下是一套**从零做到一**的教程，按实现顺序排列，每章结尾指向下一章：

| 章节 | 内容 |
|---|---|
| [00 项目概述](docs/00-项目概述.md) | 项目是什么、技术选型、整体架构 |
| [01 LLM 抽象层](docs/01-LLM抽象层.md) | 依赖倒置 + 工厂模式，模型可切换 |
| [02 工具系统](docs/02-工具系统.md) | 说明书 / 本体 / 契约层 + 角色级隔离 |
| [03 数据库设计](docs/03-数据库设计.md) | 9 张表 + 金额存分不存元 + 异步 ORM |
| [04 Agent 引擎](docs/04-Agent引擎.md) | 48 行状态图 + ReAct 循环 |
| [05 多 Agent 编排](docs/05-多Agent编排.md) | supervisor 路由 + 并行执行 + 技能层 |
| [06 混合检索 RAG](docs/06-混合检索RAG.md) | 结构化切块 + BM25/向量/RRF + 重排 |
| [07 记忆系统](docs/07-记忆系统.md) | 三层记忆 + 增量合并 + 双存储对账 |
| [08 API 与认证](docs/08-API与认证.md) | JWT + 依赖注入 + SSE 流式 |
| [09 前端与总结](docs/09-前端与总结.md) | 事件流 → 执行时间线 + 五条主线总结 |
| [11 测试与评测](docs/11-测试与评测.md) | 136 项离线用例 + 五把评测尺子（切分/检索/生成/编排） |

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端 | FastAPI · Uvicorn · SQLAlchemy 2.0（async） · SQLite |
| LLM | 通义千问 DashScope（`BaseLLM` 抽象，可切 OpenAI 兼容接口） |
| 检索 | Qdrant（本地模式） · rank-bm25 · text-embedding-v3 · gte-rerank-v2 |
| 前端 | React 19 · TypeScript · Vite · Ant Design · @ant-design/plots |
| 认证 | JWT（PyJWT）+ bcrypt |
| 测试 | pytest · pytest-asyncio |

---

## 说明

这是一个**个人学习与求职项目**：目的是把「Agent 到底怎么跑起来的」这件事从底层实现一遍，
所以刻意避开了现成编排框架，把状态图、ReAct 循环、混合检索、记忆合并这些都自己写了一遍。
`docs/` 里记录的每一个坑都是真实踩过的，不是转述。
