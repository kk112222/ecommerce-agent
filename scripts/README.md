# scripts/ 目录说明

这里放**工具 / 评测 / 冒烟**脚本。

> ⚠️ **自动化测试不在这里** —— 在 `../tests/`（pytest，离线假 LLM、无需 `.env`）：
> `PYTHONPATH=. .venv/Scripts/python -m pytest`
>
> 本目录的 `smoke_*.py` 只是"手动跑一下看链路通不通"的打印脚本，**没有断言、不是测试**。

---

## 初始化 / 构建（首次运行）

| 脚本 | 用途 | 依赖 |
|---|---|---|
| `init_db.py` | 建表（`drop_all` 重建，开发期用） | 无 |
| `seed_data.py` | 造模拟数据（过去 30 天） | 无 |
| `build_kb.py` | 知识库：解析 MD/PDF/DOCX/HTML → 结构化切块 → embedding → 存 qdrant | DashScope；**须先停后端**（qdrant 单进程锁） |

## 日常使用

| 脚本 | 用途 | 依赖 |
|---|---|---|
| `cli.py` | 命令行直接和 Agent 对话 | `.env` 的 API key |

## 评测（出数字，简历/面试用）

| 脚本 | 用途 | 依赖 |
|---|---|---|
| `rag_eval.py` | 检索质量：51 题 golden set，BM25 / 向量 / 混合 RRF / 混合+精排 四策略 A/B（Recall@5、Hit@1、MRR） | DashScope；**须先停后端** |
| `rag_quality_eval.py` | 生成侧：扰动测试 + judge 忠实度 | 只要 LLM，不碰 qdrant，**后端不用停** |

## 冒烟脚本（手工跑，无断言，要真 LLM）

| 脚本 | 覆盖 |
|---|---|
| `smoke_llm.py` | LLM 抽象层：普通对话 + 流式对话 |
| `smoke_agent.py` / `smoke_agent_llm.py` | 单个 ReActAgent 跑通 |
| `smoke_planner.py` / `smoke_executor.py` / `smoke_synthesizer.py` | supervisor 各节点单独冒烟 |
| `smoke_supervisor.py` | 整条多 Agent 图（intent → 各链路）跑通 |
| `smoke_sse.py` | SSE 事件流（plan → subtask → report） |

## 验证脚本（`verify_*`，带断言 / 端到端）

| 脚本 | 用途 | 依赖 |
|---|---|---|
| `verify_memory_incremental.py` | 长期记忆增量式端到端（merge / episodic / recall / 管理） | DB + qdrant；**须先停后端** |
| `verify_context_fix.py` | 上下文 / 上传注入截断 + 记忆时间戳 | DB |
| `verify_viz_stream.py` | 聊天自适应图表 `viz` 块 | **须后端存活** + SSE |
| `verify_stage2.py` / `verify_stage2_db.py` | 阶段2 向量记忆历史验收（A 需后端存活；B 须先停后端） | 后端 / DB |
| `verify_document_agent.py` | 文档 Agent 离线验证（stub LLM） | 无（核心断言已并入 `tests/`） |
| `verify_tool_args.py` | 工具参数统一校验（stub LLM） | 无（核心断言已并入 `tests/`） |

## 一次性

| 脚本 | 用途 |
|---|---|
| `backfill_legacy_memory.py` | 把旧 qdrant 记忆点回填成 `long_term_memories` 行（2026-09-08 记忆改造后跑过一次，幂等可重跑） |

---

## 通用约定

- 一律用项目 venv 跑：`PYTHONPATH=. .venv/Scripts/python scripts/xxx.py`
- 往 Windows 终端打中文/emoji 要 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
- **qdrant 本地模式是单进程独占**：任何要读 qdrant 的脚本，跑之前先停后端（8000 端口）
