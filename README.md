# 企业知识库 AI 助手（后端）

> 基于 FastAPI + pgvector + LLM Function Calling 的企业知识库 RAG / Agent 后端。功能包括文档摄取、混合检索、SSE 流式问答、ReAct Agent、Prompt 版本化、RAG 自动评估，以及第 18 章规模化的三个独立模块：多模型负载均衡、分布式向量分片、评估 CI。

[![Python](https://img.shields.io/badge/Python-3.10+-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.114+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-336791?logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 项目定位

面向企业知识库场景的 RAG / Agent **后端服务**。目标是把"上传文档 → 切分入库 → 检索增强 → LLM 回答"这条链路做到可观测、可评估、可降级。

> **个人贡献声明**：项目的整体架构、RAG / Agent 链路设计、第 18 章三个规模化模块的方案、数据库 schema 与 Alembic 迁移、评估指标与 CI 脚本均由本人主导设计与定稿；具体编码过程中借助 AI 编程助手（Claude Code）作为 pair-programmer 提升落地效率，所有代码经本人逐文件审阅、调试、跑通验证后入库。`frontend/` 目录是 FastAPI 模板自带的 React 演示页 + 为联调 AI 功能补齐的最小页面，仅用于本地端到端验证，不计入个人前端亮点。

---

## 核心能力

### 1. 检索增强问答（RAG）
- 文档摄取：上传 → 异步处理 → 多策略切分（固定窗口 / Markdown 标题 / 段落语义）→ Embedding → 入库。
- 混合检索：pgvector 向量召回 + PostgreSQL 全文检索（`tsvector + ts_rank`）双路召回 → RRF 融合 → Cross-Encoder Rerank 收敛到 Top-K。
- 流式回答：SSE 事件序列 `retrieval → token* → done`，前端可渐进式渲染。
- 可解释性：每次检索写入 `retrieval_logs`，记录召回 chunk_id、各阶段分数、是否拒答、延迟。
- 拒答机制：相似度阈值低于 `REFUSAL_THRESHOLD` 时直接拒答，避免幻觉。

### 2. Agent 工具循环
- Function Calling 驱动的 ReAct 循环，最大步数限制（`AGENT_MAX_STEPS`）防止失控。
- 内置工具：`search_knowledge_base` / `get_document_meta` / `kb_info` / `mcp_tool`（MCP Bridge）。
- 工具结构以 OpenAI function-calling schema 暴露，可被前端按需直调（`POST /tools/run`）。
- MCP 桥接：通过 `mcp_tool` 接入外部 MCP Server，扩展工具集。

### 3. Prompt 版本化与热切换
- `prompt_configs` 表存储 system / retrieval / answer 三段模板，`(name, version)` 唯一。
- 同名 Prompt 仅一条 `is_active`，`POST /prompts/{id}/activate` 原子切换。
- 每条对话写入 `prompt_version`，支持效果归因与 A/B。

### 4. 规模化模块（设计文档第 18 章）
| 模块 | 文件 | 解决的问题 |
| --- | --- | --- |
| 多模型负载均衡 | `app/services/llm_router.py` | 加权轮询 / 最少在途 / 成本路由 + 熔断 + 限速感知 + 流式兼容 fallback |
| 分布式向量分片 | `app/services/vector_store.py`、`shard_router.py` | `kb_id` 哈希分片，单分片定向路由 + scatter-gather 全局检索；业务层只依赖 `VectorStore` 抽象 |
| RAG 自动化评估 + CI | `app/services/eval_service.py`、`scripts/eval_ci.py` | Hit@k / MRR / Recall@k / Context-Precision / Faithfulness / 平均延迟 / 估算成本 / 拒答率 |

三个模块都可独立运行，文件末尾自带 `__main__` demo，`mock` provider 模式无需任何 API key 即可观察调度决策、分片路由、评估指标。

### 5. 可观测性 / 运维
- Langfuse Trace（开关 `LANGFUSE_ENABLED`）。
- Token 计费：`token_usages` 表按 session / user / model 维度落盘 prompt / completion / 估算成本。
- 限流：Redis 按用户每分钟令牌桶（`RATE_LIMIT_PER_MINUTE`），命中返回 429。
- 降级链：embedding / rerank / pdf 解析在依赖缺失时自动降级，保证最小可运行。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| Web 框架 | FastAPI（async） + Starlette SSE |
| ORM / 迁移 | SQLModel（异步） + Alembic（单 head 迁移 `a1b2c3d4e5f6`） |
| 数据库 | PostgreSQL 17 + **pgvector**（向量） + `tsvector`（全文） |
| 缓存 / 限流 | Redis 7 |
| LLM SDK | `openai` / `anthropic`，统一 `LLMService` 抽象 + `LLMRouter` 调度 |
| Embedding | OpenAI / Mock（确定性伪向量，本地零成本跑通） |
| 重排 | `BAAI/bge-reranker-base`（Cross-Encoder，可选） |
| 鉴权 | JWT + Argon2 / bcrypt |
| 可观测 | Langfuse（可选） |
| 部署 | Docker Compose 一键启动（db + redis + backend） |

---

## 目录结构

```
ai-knowledge/
├── backend/
│   ├── app/
│   │   ├── api/routes/         # 12 个路由模块（chat/prompts/tools/eval/kb/document/...）
│   │   ├── services/           # RAG / Agent / LLMRouter / VectorStore / Eval 核心服务
│   │   │   ├── chat_service.py        # 主问答（非流式 + SSE 流式）
│   │   │   ├── retrieval_service.py   # 向量 + BM25 + RRF + Rerank 混合检索
│   │   │   ├── agent_service.py       # ReAct + Function Calling 工具循环
│   │   │   ├── llm_router.py          # 多模型负载均衡 / 熔断 / 限速 / fallback
│   │   │   ├── vector_store.py        # VectorStore 抽象（pg / sharded）
│   │   │   ├── shard_router.py        # 向量分片路由 + scatter-gather
│   │   │   ├── eval_service.py        # RAG 评估指标计算
│   │   │   ├── prompt_service.py      # Prompt 版本化 / 激活
│   │   │   ├── tool_registry.py       # 工具注册中心 + function-calling schema
│   │   │   ├── tools/                 # 内置工具（kb_search / doc_meta / mcp）
│   │   │   ├── reranker.py            # Cross-Encoder Rerank（可降级）
│   │   │   ├── chunking.py            # 三种切分策略
│   │   │   ├── embedding_service.py   # Embedding 抽象 + Mock
│   │   │   └── observability.py       # Langfuse Trace
│   │   ├── workers/document_tasks.py  # 文档异步处理
│   │   ├── core/                      # config / db / rate_limit / security
│   │   ├── alembic/versions/          # 数据库迁移（v2 AI 表见 a1b2c3d4e5f6_v2_ai_tables.py）
│   │   ├── models.py                  # 全部 SQLModel（含 pgvector / tsvector 列）
│   │   └── main.py                    # FastAPI app 入口
│   ├── scripts/eval_ci.py             # 评估 CI 脚本（指标阈值守门）
│   ├── tests/                         # pytest 单测 + API 集成
│   └── requirements.txt
├── frontend/                          # 模板自带 React 演示页（仅本地联调用）
├── docs/                              # 设计文档 / 架构 / 快速开始 / API / 规模化
├── notes/                             # 开发踩坑记录（pgvector / SSE / Agent / 多模型）
├── compose.local.yml                  # 一键启动（db + redis + backend）
└── .env.example
```

---

## 快速开始

### 方式一：Docker Compose 一键启动（推荐）

```bash
cp .env.example .env
docker compose -f compose.local.yml up --build
```

启动后：
- 后端：http://localhost:8000
- OpenAPI 文档：http://localhost:8000/docs
- 数据库：localhost:5432
- Redis：localhost:6379

`.env` 默认 `EMBEDDING_PROVIDER=mock` + `LLM_PROVIDER=mock`，无需任何 API key 即可跑通整条链路（上传 / 切分 / 检索 / 流式问答 / Agent / 评估）。

### 方式二：本地 Python 启动

```bash
cd backend
pip install -r requirements.txt
# 自行启动 Postgres(pgvector) + Redis 后
alembic upgrade head
fastapi run app/main.py --host 0.0.0.0 --port 8000 --reload
```

### 验证

```bash
# 1. 注册 / 登录获取 token
curl -X POST http://localhost:8000/api/v1/login/access-token \
  -d "username=admin@example.com&password=changethis"

# 2. 创建知识库 / 上传文档 / 提问 详见 docs/api.md
```

### 跑一遍规模化模块的 demo（无需 DB / API key）

```bash
cd backend
python -m app.services.llm_router      # 观察加权轮询 / 熔断 / fallback 决策
python -m app.services.shard_router    # 观察分片路由 + scatter-gather
python scripts/eval_ci.py              # 跑内置标注集，输出 Hit@k / MRR / 延迟 / 成本
```

---

## 关键 API 一览

| Method | Path | 说明 |
| --- | --- | --- |
| POST | `/api/v1/knowledge-bases/` | 创建知识库 |
| POST | `/api/v1/documents/` | 上传文档（异步处理） |
| POST | `/api/v1/chat/sessions` | 创建会话（绑定知识库） |
| POST | `/api/v1/chat/sessions/{id}/ask` | RAG 主问答（非流式） |
| POST | `/api/v1/chat/sessions/{id}/ask/stream` | **SSE 流式问答** |
| GET | `/api/v1/tools/` | 列出 Agent 可用工具的 function-calling schema |
| POST | `/api/v1/tools/run` | 直接调用单个工具（不走 Agent 循环） |
| POST | `/api/v1/prompts/` | 新建 Prompt 配置 |
| POST | `/api/v1/prompts/{id}/activate` | 激活 Prompt 版本（同 name 互斥） |
| POST | `/api/v1/eval/run` | 跑 RAG 评估，返回核心指标 |

完整 OpenAPI：http://localhost:8000/docs ，详细字段见 [`docs/api.md`](docs/api.md)。

---

## 数据库要点

- 向量列：`document_chunks.embedding vector(1536)`，迁移内创建 HNSW 索引。
- 全文列：`document_chunks.tsv tsvector`，触发器自动同步 `content`。
- 冗余键：`document_chunks.knowledge_base_id` / `owner_id` 冗余，便于检索时按 KB / 用户直接过滤（也是分片路由键）。
- 审计表：`retrieval_logs`（检索过程）/ `token_usages`（token & 成本）/ `prompt_configs`（Prompt 版本）。
- 所有原始 SQL 走 `sqlalchemy.text` 参数化执行，规避注入。

---

## 文档

- [`docs/design-v2.md`](docs/design-v2.md) — **权威设计文档 v2**（含第 18 章规模化方向）。代码注释里所有 `# 设计文档 X.Y` 都对应此文档章节。
- [`docs/architecture.md`](docs/architecture.md) — 系统架构 / 调用链路 / 关键时序图
- [`docs/quickstart.md`](docs/quickstart.md) — 详细的本地搭建与 smoke-test 步骤
- [`docs/api.md`](docs/api.md) — REST API 字段级说明
- [`docs/scaling.md`](docs/scaling.md) — 第 18 章规模化模块（LLMRouter / ShardRouter / Eval CI）设计与运行
- [`docs/rag-pipeline.md`](docs/rag-pipeline.md) — RAG 检索链路（切分 / 召回 / 融合 / 重排 / 拒答）
- [`docs/agent-design.md`](docs/agent-design.md) — Agent 工具循环、MCP 桥接、function-calling 协议
- [`notes/`](notes/) — 开发过程中的踩坑记录（pgvector / SSE / Agent / 多模型降级）

---

## 设计取舍

- mock provider 优先：embedding / LLM / rerank / pdf 解析都提供 mock 或自动降级，评审 / 面试官不需要任何 key 也能端到端跑通。
- 抽象先行：`VectorStore` / `LLMService` / `EmbeddingService` 都是接口先行，单机到分片、单模型到多模型只换实现，业务层不动。
- 审计先行：检索日志、token usage、prompt version 在 v1 阶段就落表，避免效果回归时无据可查。
- 拒答优先于硬答：低置信度直接拒答，比"硬答"更符合企业知识库的可信要求。

---

## License

MIT，见 [`LICENSE`](LICENSE)。
