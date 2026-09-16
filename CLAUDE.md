# 掌柜 · 电商运营 AI Agent 平台

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

## 当前完成状态（2026-09-13）

### 已完成 ✅

**Phase 0 - 基础抽象层：**
- `backend/core/llm/` — LLM 抽象接口（BaseLLM + Message + ToolCall）+ 千问实现 + 工厂函数
- `backend/core/tool/` — ToolSpec/ToolResult/BaseTool + ToolRegistry 注册中心
- `backend/core/agent/base.py` — 自研 AgentGraph 状态图引擎（兼容 async + 条件路由）
- `backend/core/config.py` — Pydantic Settings，extra="ignore"

**Phase 1 - 数据层 + 数据工具：**
- DB: SQLite + aiosqlite + SQLAlchemy 2.0 async
- 模型: Product / Order / User（金额存分避免浮点精度问题）
- 5 个数据分析工具全部注册可用（名字以 `registry.tools` 为准，曾被文档写成 `sales_query`/`stock_alert`）：
  - `query_sales` — 按日期查销售额
  - `category_query` — 按分类+日期查
  - `check_stock` — 库存低于阈值
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
- `POST /api/chat/stream` — SSE 流式（意图→计划→子任务→报告逐字）
- `GET /api/dashboard` — 数据看板
- `GET /api/dashboard/insight` — AI 经营洞察（数据快照喂 LLM 生成自然语言解读）
- `POST /api/auth/*` — 注册/登录，JWT 鉴权
- `POST /api/upload` — 文件上传解析（挂在会话下，供竞品对比）
- `GET /api/sessions` + `GET/PATCH/DELETE /api/sessions/{sid}` — 多会话列表/历史/重命名/删除
- `GET /api/memory` + `DELETE /api/memory/{id}` — 长期记忆可查看/可删除（+ 提炼计数）
- `GET /api/documents` + `GET /api/documents/{sid}/{filename}` — 生成物列表/下载（归属校验）
- 会话已持久化：`chat_messages`（消息）+ `chat_sessions`（会话元信息）两张表

**前端：**
- React + TypeScript + Ant Design + @ant-design/plots（看板图表）
- App.tsx: 功能侧边栏（智能问答/数据看板）+ 会话状态管理（chatKey 重挂载切换会话）
- SessionSidebar.tsx: 多会话列表（新建/切换/删除）
- ChatPage.tsx: 聊天界面（历史加载、SSE 流式、上传对比；SSE 事件 → 执行时间线状态机）
- TracePanel.tsx: 执行时间线（意图→计划→子任务→报告 live 化：状态动效 + 耗时 + 工具提示）
- DashboardPage.tsx: 数据看板（统计卡 + 图表 + AI 经营洞察卡片）
- api.ts: SSE 流式读取 + 会话 CRUD + 洞察接口封装

**脚本：**
- `scripts/init_db.py` — 建表
- `scripts/seed_data.py` — 模拟数据
- `scripts/cli.py` — CLI 命令行 chat
- `scripts/build_kb.py` — 知识库构建（自动识别格式→切块→embedding→入库）
- `scripts/smoke_*.py` — 手工冒烟脚本（无断言、要真 LLM/网络，用来手动看链路通不通）
- **自动化测试在 `tests/`**（pytest，离线假 LLM、无需 .env）：`PYTHONPATH=. .venv/Scripts/python -m pytest`

### 增补（08-28 → 09-08）

- **会话重命名 + 彻底删除 ✅（08-28，0a1aa18）**：SessionSidebar 编辑态（hover/双击进 Input，Enter 提交）＋删除二次确认；后端 delete 从软删升物理删除（`delete(ChatMessage/ChatSession)`）。后续修 `d5ba43c`：物理删除连带清 uploaded_doc
- **AI 洞察生成提速 ✅（08-28，0e720ec）**：当天缓存秒开（insight_cache 表，startup 幂等 create_all 补表）+ `?refresh=true` 强制重生成；LLM 失败不写缓存
- **上下文/上传注入超长保护 + 记忆打时间戳 ✅（09-04，ada22eb）**：`get_messages` 历史 MAX_HISTORY_CHARS=8000 从最旧逐条丢；上传文档注入 MAX_DOC_CHARS=6000 截断并标注原文总字数；`save_user_memories` payload 带 created_at（scripts/verify_context_fix.py）
- **聊天输出自适应可视化 ✅（09-04，0677a8c）**：Synthesizer prompt 末尾注入 `VIZ_RULES`——结论适合图/表时在报告最末尾追加且仅一个 ```` ```viz ```` JSON 块（type 限 line/bar/column/table，数值必须逐字照抄子任务结果、禁止编造）。纯文本协议→零 schema 改动、历史重放免费。前端 `VizBlock.tsx`（parseViz 抽块 + Area/Column/Bar/Table 渲染）三层容错：块未闭合占位 Spin / JSON 坏或未知 type 弱提示不崩
- **SSE 链路异常兜底 ✅（09-04，0677a8c）**：run_graph try/except/finally——失败也推 error 事件 + **finally 必送 None 哨兵**，否则 LLM 挂了 SSE 永久挂起、前端无限 loading；前端 switch 加 error case（标红 + message.error + 结束 loading）
- **模型切换 ✅**：当前对话模型 qwen3.8-27b（改 .env `LLM_MODEL`，改完重启后端生效）。坑：DashScope 对话模型免费额度独立于 embedding/rerank（403 只挂聊天、RAG 正常）
- **长期记忆大改造 ✅（09-08，fe5364e）**：接外部评审 M1/M3/M4/M5/M6，三层记忆从"删光重建"改**增量式**。SQLite 行 = 唯一事实源（新表 `long_term_memories`），qdrant 只当向量索引。kind 分桶：semantic（语义画像，difflib 增量合并：≥0.90 同条刷新 / 0.45~0.90 作废旧行重写 / <0.45 新增）/ episodic（情景记忆，append+去重）。extractor 双桶、原料整轮（M1）；per-user asyncio.Lock（M3）；加权召回；`GET/DELETE /api/memory` 管理接口（M6）；qdrant_client 加 delete_points。验证：scripts/verify_memory_incremental.py 全绿；旧 qdrant 13 点已由 scripts/backfill_legacy_memory.py 幂等回填成行。边界：M2 只做寒暄跳过轻量版、episodic 默认不过期、管理 UI 未做

### 增补（09-13，对照 docs/13 缺陷清单逐条修）

- **生成文档闭环 ✅（09-13）**：文档 Agent 落盘 → SSE `document` 事件 → 前端"本次生成的文件"卡片点一下下载。`backend/api/routes/documents.py` + `infrastructure/doc_output.py` 读侧（只取末段文件名 + 扩展名白名单 + `is_relative_to` 归属校验，别人的文件一律 404，不区分"不存在/没权限"）。验证：scripts/smoke_documents_api.py + tests/test_documents_api.py
- **长期记忆收尾 ✅（09-13）**：difflib 分不出"关注退货率 vs 关注退款率"这类平行偏好 → 0.45~0.90 歧义档交 LLM 判"更新 vs 并列"，判据坏了自动退回旧行为；"从未被认领"的作废加 50%/3 行护栏（避免单轮把画像清空）；召回不再灌 importance（热度 `last_access_at` 与语义权重 `importance` 分离）；episodic 加 30 天 TTL；SQLite↔qdrant 无共享事务 → `reconcile_memory()` 双向对账（补丢的向量/清孤儿点，`--dry-run` 可用）挂在 lifespan
- **提炼任务可靠性 ✅（09-13）**：`create_task` 返回值持强引用（否则任务可能被 GC，"记忆时好时坏"）；per-user 锁换 `WeakValueDictionary`（原来只增不减）；失败落 `memory_extract_tasks` 表 + 启动重放 + `/api/memory` 暴露 ok/skipped/failed/retried 计数
- **LLM 预算与用量 ✅（09-13）**：`BudgetedLLM` 装饰器包住 BaseLLM —— 预算必须在 LLM 层，因为调用次数是 ReAct 循环内部攒的（4×executor×≤10 轮），Graph 节点看不见。超限由 supervisor 各节点降级（保数据出报告）而非抛 500；用量随 `/chat` 响应与 SSE `usage` 事件上前端。真机实测：45 字回答实际消耗 805 completion tokens（思考 token 不体现在可见输出里）
- **配置与健壮性 ✅（09-13）**：CORS 白名单取代 `*`、`lifespan` 取代弃用的 `on_event`、`.env` 里 DB_URL/密钥/echo 真正生效（原来配了不用）；planner 输出结构校验（兜底 id `tl`→`t1`、id 去重、缺 task 丢弃、编造工具名清空、上限 5 条）；executor 按 `tool_hint` 把 registry 收窄成子集（硬隔离，不再靠 prompt 软约束；粒度是**单工具** —— 需要多个工具的子任务靠"拆成多条"解决，不是靠放宽 hint）；registry 按 spec 做类型校验 + 兜住工具本体异常；KB 按**语料内容指纹**失效缓存（重建知识库不用重启）。测试从 5 个文件扩到 15 个（108 项，全离线）
- **修复轮后自查出并处理掉 3 条 ✅（09-14，docs/13 第十节）**：① planner id 重编号死循环（`len()` 在循环体内是常量 → 同步 CPU 自旋，堵的是整个事件循环，faulthandler 实锤）；② `executor_node` 的裸 `asyncio.gather` 没有异常隔离 —— 任一子任务抛非预算异常（LLM 500/超时）都会中断整轮、丢掉其余子任务已查好的结果，现在 `run_one` 逐格降级 + `return_exceptions=True` 兜底；③ `tool_hint` 单工具硬隔离"收得太紧" —— 需要两个工具的子任务会静默给出残缺结论，而填了多个工具名又会被清空、放开全集（守规矩的被削、不守规矩的放开），现在把 planner 规则**硬化**成"一条子任务一个工具，需要多个就拆条" + 降级路径补 warning。三条都补了回归用例，且都**先验证过用例在修复前会失败**

### 增补（09-16，工具边界从「子任务级」上移到「角色级」）

- **角色级工具范围 ✅（09-16，591ce3e）**：原来 executor 按 planner 的 tool_hint 把注册中心收窄成**单个工具** —— 粒度绑错层：子任务是「执行实例」不是「职责单元」，planner 拆解不准时会**静默给出残缺结论**（守规矩的被削、不守规矩的反因 hint 非法而放开全集）。现在 registry 加 `TOOL_SCOPES` + `subset_scope()`：executor 固定拿 analysis 角色的 5 个数据工具；tool_hint 降级为 prompt 里「建议优先使用」的提示；**跨角色越界（分析任务想写文件）仍被 schema 挡住**。planner 硬规则改为「按要回答的问题拆、不按工具拆」。测试 14 项（角色范围 / 同角色连续调两个工具 / 跨角色拦截 / 未知范围降级告警），全量 110 项无失败。

### 尚未完成 ❌

- Docker Compose 部署（后端/前端/数据库一键起）
- Alembic 迁移（开发期 init_db 直接 drop_all 够用，上线前换迁移）
- 上线前优化：LLM 供应商切换配置化（BASE_URL 仍硬编码 DashScope）、前端 bundle code splitting（当前 2.3MB）

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
│   │   ├── llm/                    # LLM 接口（base/qwen/factory + budget 预算/用量）
│   │   ├── tool/                   # 工具系统（base/registry，含参数校验与工具子集隔离）
│   │   ├── agent/                  # AgentGraph 状态图引擎
│   │   ├── memory/                 # 记忆系统（消息/会话/画像/提炼 + 增量合并 + 对账）
│   │   └── config.py               # 全局配置（.env → Pydantic）
│   │
│   ├── agents/                     # Agent 实现（supervisor 只做调度，人格/上下文收在各角色里）
│   │   ├── supervisor.py           # 状态图组装 + 条件路由（intent → 4 条链路）
│   │   ├── intent_classifier.py    # 意图分类（analysis/content/service/document）
│   │   ├── planner.py              # 拆子任务（输出做结构校验，P2-13）
│   │   ├── executor.py             # 子任务执行（按角色收窄工具集 subset_scope；tool_hint 只是提示）
│   │   ├── synthesizer.py          # 综合各子任务结果 → 报告（流式 / 可选 viz 块）
│   │   ├── data_analysis/simple_agent.py  # ReActAgent（核心循环）
│   │   ├── content_gen/            # 内容 Agent（文案/标题）
│   │   ├── customer_service/       # 客服 Agent（查知识库）
│   │   └── document/               # 文档 Agent（解析/优化/生成落盘）
│   │
│   ├── tools/                      # ★ 业务工具（三层注册）
│   │   ├── data_tools/             # 5 个数据工具 + __init__ 注册
│   │   ├── content_tools/          # 2 个内容工具（需注入 llm）+ __init__
│   │   ├── service_tools/          # RAGTool（按语料指纹缓存失效）+ __init__
│   │   ├── document_tools/         # 解析/优化/写文档（落盘后回调推 SSE 事件）
│   │   └── __init__.py             # register_all_tools() 总入口
│   │
│   ├── api/                        # FastAPI
│   │   ├── app.py                  # 应用入口 + lifespan（建表/记忆对账/提炼重放）+ CORS 白名单
│   │   ├── routes/chat.py          # /api/chat + /api/chat/stream（SSE）+ 提炼后台任务
│   │   ├── routes/auth.py          # 注册/登录（JWT）
│   │   ├── routes/dashboard.py     # /api/dashboard + /api/dashboard/insight
│   │   ├── routes/upload.py        # /api/upload 文件解析
│   │   ├── routes/session.py       # /api/sessions 多会话 CRUD
│   │   ├── routes/memory.py        # /api/memory 长期记忆查看/删除 + 提炼计数
│   │   ├── routes/documents.py     # /api/documents 生成物列表/下载（归属校验）
│   │   └── schemas/chat.py         # Pydantic 模型
│   │
│   ├── db/                         # 数据库
│   │   ├── models/                 # Product/Order/User/ChatMessage/ChatSession/UserProfile/
│   │   │                           #   UploadedDoc/LongTermMemory/InsightCache/MemoryExtractTask
│   │   ├── session.py              # 异步引擎 + sessionmaker
│   │   └── migrations/             # Alembic（空壳）
│   │
│   ├── infrastructure/
│   │   ├── doc_output.py           # 生成物的落盘/归属路径解析（路径安全双保险）
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
│       ├── App.tsx                 # 应用入口 + 会话状态管理
│       ├── ChatPage.tsx            # 聊天界面（SSE → 时间线状态机）
│       ├── SessionSidebar.tsx      # 多会话列表
│       ├── TracePanel.tsx          # 执行时间线
│       ├── DashboardPage.tsx       # 数据看板 + AI 洞察
│       ├── VizBlock.tsx            # 报告里的 ```viz 块 → 图表/表格
│       ├── LoginPage.tsx           # 登录
│       └── api.ts                  # API 封装（SSE/会话/洞察/文档下载）
│
├── scripts/
│   ├── init_db.py                  # 建表
│   ├── seed_data.py                # 模拟数据
│   ├── build_kb.py                 # 知识库构建
│   ├── cli.py                      # CLI 命令行
│   ├── reconcile_memory.py         # 记忆对账（SQLite ↔ qdrant，支持 --dry-run）
│   ├── smoke_*.py                  # 手工冒烟（真 LLM，非测试）
│   ├── verify_*.py                 # 单点验证脚本（工具参数/记忆增量/预算…）
│   ├── rag_eval.py                 # RAG 检索评测（出数字）
│   └── rag_quality_eval.py         # RAG 切分质量评测
│
├── tests/                          # pytest 离线测试套件（假 LLM，无需 .env）
│
├── docs/                           # 章节文档（00/01/10/12/13 + 职业规划）
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

### 7. 两段式检索（粗排 + 精排）
RRF 融合 BM25+向量是"粗排"（快而糙，只比排名位置），再用 CrossEncoder（gte-rerank-v2）对 top-10 语义精排（慢而准）。精排增益通常比加大召回更明显，是 RAG 检索质量的常规兜底。

### 8. 会话元信息与消息分离（多会话）
`chat_messages` 只管消息，`chat_sessions` 管会话本身（标题/最近活跃/软删除）。侧边栏列表只查小表，不扫消息表；软删除留恢复口子。

### 9. Agent 过程可观测（live 时间线）
Agent 最大的缺点是黑盒。后端 SSE 事件（intent/plan/subtask/token/report）驱动前端执行时间线：进行中转圈、完成打勾 + 耗时、失败标红，子任务带工具提示和结果摘要。让运营看到"它真在干活"，增强信任，也是差异化。

### 10. 看板会说话（AI 经营洞察）
看板只有当前值、没有判断依据（涨跌不知道、风险看不出）。洞察接口把比看板更全的快照（今日/昨日/上周对比、库存明细、Top 商品）喂给 LLM 生成自然语言解读。复用分析 Agent 的能力，让"数据陈列"变"经营解读"。

---

## Agent 的调用链路（每次对话都走这个）

```
用户输入 → API(/api/chat) → ReActAgent.run()
  └→ while 循环（最多 10 轮）:
       ├→ llm.chat(messages, tools=所有工具的JSON Schema)
       ├→ LLM 返回: "我需要调 query_sales(start_date=...)"
       ├→ registry.execute("query_sales", start_date=..., end_date=...)
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
