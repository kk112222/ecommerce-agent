# 电商运营 AI Agent 平台

## 项目概要

| 项 | 内容 |
|---|---|
| **作者** | 代洋，成都工业学院，数据科学与大数据技术，大二 |
| **目标** | 找 AI Agent 实习，需要一个能讲深讲透的完整项目 |
| **定位** | 电商运营 AI 助手 — 给内部运营团队用，**不是给顾客用的** |
| **核心能力** | 数据分析 / 内容生成 / 知识库检索（RAG） |
| **周期** | 1-2 个月（8 周） |
| **运行方式** | 先本地用 SQLite + Qdrant 本地模式跑通，再上线 |
| **LLM** | 默认通义千问 DashScope，接口可切换 |

---

## 给 Claude 的上下文

1.  这个项目的用户是**电商运营人员**，不是顾客。所有工具都是内部运营场景——查销售数据、查库存、生成文案、查退货政策/FAQ。
2.  用户的水平是大二，正在学 Python + AI，SQLAlchemy 和 async/await 是跟着项目刚学的。
3.  用户偏好：用中文解释原理、代码注释用中文、路径用 r"" 原始字符串、不装未经确认的包。
4.  最关键的教学原则：**"知其所以然"**——每个设计决策用户都要理解为什么。只讲"怎么做"会被追问"为什么这么做"。
5.  用户不喜欢一次性被塞 6 个文件（上次 RAG 系统翻车了），新概念要逐层讲，和已理解的概念做类比。

---

## 当前完成状态（2025-08-05）

### 已完成 ✅

**Phase 0 - 基础抽象层：**
- `backend/core/llm/` — LLM 抽象接口（BaseLLM + Message + ToolCall）+ 千问实现 + 工厂函数
- `backend/core/tool/` — ToolSpec/ToolResult/BaseTool + ToolRegistry 注册中心
- `backend/core/agent/base.py` — 自研 AgentGraph 状态图引擎（兼容 async + 条件路由）
- `backend/core/config.py` — Pydantic Settings，extra="ignore"

**Phase 1 - 数据层 + 数据工具：**
- DB: SQLite + aiosqlite + SQLAlchemy 2.0 async
- 模型: Product / Order / User（金额存分避免浮点精度问题）
- 5 个数据分析工具全部注册可用：
  - `sales_query` — 按日期查销售额
  - `category_query` — 按分类+日期查
  - `stock_alert` — 库存低于阈值
  - `user_profile` — 按会员等级统计
  - `product_query` — 按商品名模糊搜索
- 模拟数据: 8 个中文商品 + 5 个用户 + 100 条订单

**Phase 2 - 内容工具：**
- `title_optimizer` — SEO 标题优化（构造函数注入 llm 实例）
- `copy_generator` — 营销文案生成，参数含平台/风格/字数

**Phase 3 - RAG + Agent 升级：**
- ReActAgent: if → while 循环，LLM 自主决定调用多少次工具
- SSE 流式推送：状态 → 工具调用 → 工具结果 → token 逐字
- 工具全部统一注册到同一个 ToolRegistry
- RAG 系统完整跑通：
  - 3 个 .md 知识库文档（退货/配送/FAQ）
  - `embeddings.py` — 千问 text-embedding-v3 向量化
  - `qdrant_client.py` — Qdrant 本地模式存储
  - `retriever.py` — BM25 + 向量 + RRF 混合检索
  - `doc_processor.py` — 多格式解析（MD/PDF/DOCX/HTML）
  - `rag_search.py` — RAGTool，和 SalesQueryTool 一样继承 BaseTool
  - 全链路测试通过：问"怎么退货？运费谁出？"→ 自动调 search_knowledge_base → 正确回答

**API 层：**
- `POST /api/chat` — 普通回复
- `POST /api/chat/stream` — SSE 流式（看到思考过程）
- `GET /api/dashboard` — 数据看板
- sessions 目前是内存字典（`sessions: dict[str, list[Message]]`），重启丢失

**前端：**
- React + TypeScript + Ant Design
- ChatPage.tsx: 聊天界面 + 数据看板卡片
- api.ts: sendMessageStream() SSE 流式读取，done 事件正确处理

**脚本：**
- `scripts/init_db.py` — 建表
- `scripts/seed_data.py` — 模拟数据
- `scripts/cli.py` — CLI 命令行 chat
- `scripts/build_kb.py` — 知识库构建（自动识别格式→切块→embedding→入库）
- `scripts/test_agent.py` / `test_agent_llm.py` / `test_llm.py` — 测试脚本

### 尚未完成 ❌

- 用户登录 + JWT 认证
- sessions 持久化（替换内存字典 → SQLite/Redis）
- Cross-Encoder 重排序（retriever.py 中已预留）
- 个性化记忆（用户画像注入查询）
- Docker Compose 部署
- 前端近 7 日销售趋势折线图

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端框架 | FastAPI + Uvicorn |
| LLM | 通义千问 DashScope（可切换） |
| 向量库 | Qdrant（本地文件模式） |
| 数据库 | SQLite + aiosqlite（开发） / PostgreSQL（上线） |
| ORM | SQLAlchemy 2.0 异步 |
| 前端 | React + TypeScript + Ant Design |
| 部署 | 待 Docker Compose |

---

## 项目结构（当前实际）

```
ecommerce-agent/
│
├── backend/
│   ├── core/                       # ★ 核心抽象层
│   │   ├── llm/                    # LLM 接口（base/qwen/factory）
│   │   ├── tool/                   # 工具系统（base/registry）
│   │   ├── agent/                  # AgentGraph 状态图引擎
│   │   ├── memory/                 # 会话记忆（空壳待填）
│   │   └── config.py               # 全局配置（.env → Pydantic）
│   │
│   ├── agents/                     # Agent 实现
│   │   ├── data_analysis/simple_agent.py  # ReActAgent（核心循环）
│   │   ├── content_gen/            # 内容 Agent（空壳）
│   │   └── customer_service/       # 客服 Agent（空壳）
│   │
│   ├── tools/                      # ★ 业务工具（三层注册）
│   │   ├── data_tools/             # 5 个数据工具 + __init__ 注册
│   │   ├── content_tools/          # 2 个内容工具（需注入 llm）+ __init__
│   │   ├── service_tools/          # RAGTool + __init__
│   │   └── __init__.py             # register_all_tools() 总入口
│   │
│   ├── api/                        # FastAPI
│   │   ├── app.py                  # 应用入口 + CORS
│   │   ├── routes/chat.py          # /api/chat + /api/chat/stream
│   │   ├── routes/dashboard.py     # /api/dashboard
│   │   └── schemas/chat.py         # Pydantic 模型
│   │
│   ├── db/                         # 数据库
│   │   ├── models/                 # Product / Order / User
│   │   ├── session.py              # 异步引擎 + sessionmaker
│   │   └── migrations/             # Alembic（空壳）
│   │
│   ├── infrastructure/
│   │   └── vector_store/           # ★ RAG 基础设施
│   │       ├── embeddings.py       #   千问 embedding API
│   │       ├── qdrant_client.py    #   Qdrant 本地模式
│   │       ├── retriever.py        #   BM25+向量+RRF 混合检索
│   │       └── doc_processor.py    #   MD/PDF/DOCX/HTML 解析
│   │
│   ├── data/kb/                    # 知识库源文档
│   │   ├── returns.md              # 退货政策
│   │   ├── shipping.md             # 物流说明
│   │   └── faq.md                  # 常见问题
│   │
│   └── services/                   # 业务服务层（空壳）
│
├── frontend/
│   └── src/
│       ├── ChatPage.tsx            # 聊天界面 + 数据看板
│       ├── api.ts                  # API 封装（含 SSE 流式）
│       └── App.tsx                 # 应用入口
│
├── scripts/
│   ├── init_db.py                  # 建表
│   ├── seed_data.py                # 模拟数据
│   ├── build_kb.py                 # 知识库构建
│   ├── cli.py                      # CLI 命令行
│   └── test_agent.py               # Agent 测试
│
├── data_v3.db                      # SQLite 数据库文件
├── qdrant_data/                    # Qdrant 本地存储
└── pyproject.toml                  # 项目配置
```

---

## 核心设计决策（"为什么"）

### 1. 依赖倒置 + 工厂模式（LLM 层）
上层代码只依赖 `BaseLLM` 抽象，不 import 具体实现。`create_llm("qwen")` 一行切换。

### 2. 工具注册中心（ToolRegistry）
每层 `__init__.py` 自注册，加新工具只需两步：写类 + 调 register。LLM 通过 `get_all_specs()` 拿到 JSON Schema 列表，自主决定调哪个。

### 3. ReAct 循环（if → while）
普通聊天机器人调一次 API 就返回。Agent 的核心是 `while` 循环：LLM 反复决定"我需要调什么工具"→ 执行 → 看结果 → 再决定，直到它认为任务完成。

### 4. 混合检索（BM25 + 向量 + RRF）
- BM25：关键词匹配（"退货"精确命中）
- 向量：语义匹配（"退钱"也能找到退货政策）
- RRF：不比较分数绝对值，只比较排名位置，更公平

### 5. 金额存分不存元
避免浮点精度问题（0.1 + 0.2 ≠ 0.3），SQLite 不支持 DECIMAL。Tools 的 execute() 里 /100 转元再返回。

### 6. SQLite 异步驱动
用 aiosqlite 而不是标准 sqlite3，因为 FastAPI 是全异步的，同步操作会阻塞其他请求。

---

## Agent 的调用链路（每次对话都走这个）

```
用户输入 → API(/api/chat) → ReActAgent.run()
  └→ while 循环（最多 10 轮）:
       ├→ llm.chat(messages, tools=所有工具的JSON Schema)
       ├→ LLM 返回: "我需要调 sales_query(start_date=...)"
       ├→ registry.execute("sales_query", start_date=..., end_date=...)
       ├→ 结果喂回 messages
       └→ 再次 llm.chat() → 如果没 tool_calls 就结束
```

---

## 如何运行

```bash
# 1. 确保 .env 里有 DASHSCOPE_API_KEY=xxx

# 2. 初始化数据
python scripts/init_db.py
python scripts/seed_data.py

# 3. 构建知识库（RAG）
python scripts/build_kb.py

# 4. 启动后端
uvicorn backend.api.app:app --host 0.0.0.0 --port 8000

# 5. 启动前端（新终端）
cd frontend
npm run dev
```

---

## 用户正在踩的关键坑

1.  **文档切分策略** — 当前按 `\n\n` 分段，300 字/块。切分方式直接影响召回质量。
2.  **召回不准确** — 向量搜回来不一定相关，需要用 RRF 融合 BM25 结果。
3.  **LLM 工具调用失败** — 参数格式错误 / 工具抛异常 / 结果为空时，ReActAgent 的 while 循环怎么处理。
4.  **数据脏/文档噪声** — PDF 扫描件、混着代码的 md、空的 docx 怎么处理。

---

## 教学原则（给 Claude 的）

当用户说"我不懂"时：
- 先问他哪里不懂，不要直接重写代码
- 用他已经懂的东西做类比（如"RAGTool 和 SalesQueryTool 一样只是 execute 里查的不一样"）
- 每次只讲一层，不一次塞 6 个文件
- 鼓励他自己写代码，你只当教练 review

当用户问"接下来做什么"时：
- 给出 2-3 个具体选项让他选一个
- 不要替他做所有决定

## 编码规范

- 回复用中文，代码注释用中文
- 路径统一使用 r"" 原始字符串
- 修改代码前先读文件确认上下文
- 修改后必须验证运行
- 不提交 .env、密钥等敏感文件

## Git 规范

- 提交格式：`<type>: <中文描述>`（feat/fix/refactor/docs/test/chore）
- 不强制推送主分支
