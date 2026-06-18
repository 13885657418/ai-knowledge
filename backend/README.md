# Backend — 企业知识库 AI 助手

> 本目录是项目核心：基于 FastAPI（async） + SQLModel + pgvector 的 RAG/Agent 后端。本文件聚焦"在 backend/ 内开发"的视角，整体说明见仓库根 [`README.md`](../README.md)，文档索引见 [`docs/`](../docs)。

## 目录结构

```
backend/
├── app/
│   ├── api/
│   │   ├── deps.py             # CurrentUser / SessionDep 依赖
│   │   ├── main.py             # APIRouter 总装
│   │   └── routes/             # 12 个路由模块
│   │       ├── login.py / users.py / utils.py / private.py
│   │       ├── knowledge_bases.py / Document.py / documentchunk.py
│   │       ├── chat.py         # /chat/sessions/* （含 SSE 流式）
│   │       ├── prompts.py      # Prompt 版本化
│   │       ├── tools.py        # 工具直调 / 列表
│   │       ├── eval.py         # RAG 评估
│   │       └── items.py
│   ├── services/               # 业务核心
│   │   ├── chat_service.py
│   │   ├── retrieval_service.py
│   │   ├── reranker.py
│   │   ├── chunking.py
│   │   ├── embedding_service.py
│   │   ├── llm_service.py
│   │   ├── llm_router.py       # ★ 多模型负载均衡 / 熔断 / fallback
│   │   ├── vector_store.py     # ★ VectorStore 抽象
│   │   ├── shard_router.py     # ★ 分布式向量分片
│   │   ├── eval_service.py     # ★ RAG 自动化评估
│   │   ├── prompt_service.py
│   │   ├── tool_registry.py
│   │   ├── tools/              # 内置工具
│   │   │   ├── base.py
│   │   │   ├── kb_search_tool.py
│   │   │   ├── kb_info_tool.py
│   │   │   ├── document_meta_tool.py
│   │   │   └── mcp_tool.py     # MCP 桥接
│   │   └── observability.py    # Langfuse Trace
│   ├── workers/
│   │   └── document_tasks.py   # 文档异步处理
│   ├── core/
│   │   ├── config.py           # Settings（Pydantic）
│   │   ├── db.py               # async engine / session
│   │   ├── rate_limit.py       # Redis 令牌桶
│   │   └── security.py         # JWT / 密码哈希
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/           # 迁移文件，head=a1b2c3d4e5f6
│   ├── schemas/                # 接口 Pydantic Schema
│   ├── eval_data/              # 内置评估标注集
│   ├── models.py               # 全部 SQLModel 表声明
│   ├── crud.py                 # 通用 CRUD
│   ├── main.py                 # FastAPI app 入口
│   ├── backend_pre_start.py    # 容器启动前等 DB
│   ├── tests_pre_start.py      # 测试前置
│   └── initial_data.py         # 初始化超管
├── scripts/
│   ├── eval_ci.py              # ★ 评估 CI 守门
│   ├── prestart.sh             # alembic upgrade + initial_data
│   ├── format.sh / lint.sh / test.sh
│   └── tests-start.sh
├── tests/                      # pytest 单测 + API 集成
├── alembic.ini
├── Dockerfile
├── pyproject.toml
└── requirements.txt
```

带 ★ 的是规模化模块，详见 [`docs/scaling.md`](../docs/scaling.md)。

## 本地开发

```bash
# 依赖
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 起 DB / Redis（建议直接用根目录 compose.local.yml）
# 然后跑迁移
alembic upgrade head

# 启动（开发模式，热重载）
fastapi run app/main.py --host 0.0.0.0 --port 8000 --reload
```

OpenAPI：http://localhost:8000/docs

## 测试

```bash
bash scripts/test.sh             # 全量
pytest tests/api -v              # 仅 API 集成
pytest tests/crud -v             # 仅 CRUD
```

## 代码风格

```bash
bash scripts/format.sh           # ruff format
bash scripts/lint.sh             # ruff check + mypy
```

## 数据库迁移

```bash
# 自动生成（基于 models.py 差异）
alembic revision --autogenerate -m "add xxx"

# 升 / 降
alembic upgrade head
alembic downgrade -1
alembic history --verbose
```

> 当前 head：`a1b2c3d4e5f6_v2_ai_tables`，包含 pgvector 列 / tsvector 列 / RAG/Agent/Eval 4 张新表。修改 models.py 后务必跑 autogenerate 再人工核对生成的迁移。

## 常用入口

| 任务 | 文件 |
| --- | --- |
| 加一个 API 路由 | `app/api/routes/*.py` + 在 `app/api/main.py` 注册 |
| 加一张表 | `app/models.py` + alembic autogenerate |
| 加一个 Agent 工具 | `app/services/tools/<your_tool>.py` 继承 `base.Tool` + 在 `tool_registry.get_registry()` 注册 |
| 改 RAG 召回逻辑 | `app/services/retrieval_service.py` |
| 改 Prompt 模板 | 用 `POST /prompts/` 接口而非改代码（支持热切换） |
| 改限流策略 | `.env` 改 `RATE_LIMIT_PER_MINUTE` 或 `app/core/rate_limit.py` |
| 加新 LLM Provider | `app/services/llm_service.py` 添加分支 + `llm_router.py` 注册 Provider |

## mock 模式（无 key 跑通）

`.env` 里：

```dotenv
EMBEDDING_PROVIDER=mock
LLM_PROVIDER=mock
REFUSAL_THRESHOLD=0.0
```

mock 模式下：
- Embedding 用哈希派生确定性向量；
- LLMService 内置 MockProvider，返回拼接型答案；
- Reranker 在缺少 sentence-transformers 时降级"分数透传"。

整条 RAG / Agent / SSE / 评估链路都能跑，只是模型推理不真实。

## 调试 tips

- **看检索结果**：`POST /tools/run` 直接调 `search_knowledge_base` 工具，跳过 LLM 看裸召回。
- **看每路得分**：查 `retrievallog` 表的 `scores` 列（jsonb），含 vector / bm25 / rrf / rerank 分。
- **复现 Agent 死循环**：把 `AGENT_MAX_STEPS` 调到 2，配合人工构造的歧义 query。
- **本地跑评估**：`python scripts/eval_ci.py`（无需任何外部依赖）。
- **看 SSE 帧**：`curl -N -X POST .../ask/stream`，每帧 `event: <name>\ndata: <json>\n\n`。

## 相关文档

- [`docs/architecture.md`](../docs/architecture.md) — 模块边界 + 调用时序
- [`docs/rag-pipeline.md`](../docs/rag-pipeline.md) — 检索链路
- [`docs/agent-design.md`](../docs/agent-design.md) — Agent ReAct 与 MCP
- [`docs/database.md`](../docs/database.md) — 表结构 / 索引 / 迁移
- [`docs/scaling.md`](../docs/scaling.md) — 规模化模块
- [`docs/api.md`](../docs/api.md) — API 字段级参考
- [`docs/deployment.md`](../docs/deployment.md) — 部署 / 运维
