# 系统架构

> 本文档描述企业知识库 AI 助手后端的整体架构、模块边界、数据流与关键时序。配套设计文档 `AI应用后端-详细设计文档-v2.md`（项目内部文档）。

## 1. 总览

```
              ┌──────────────────────────────────────────────────┐
              │                  Client / 前端                    │
              │      (React 演示页 / curl / OpenAPI Swagger)      │
              └───────────────────────┬──────────────────────────┘
                                      │ HTTPS / SSE
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │                  FastAPI（async）                         │
        │ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────┐ ┌─────────┐ │
        │ │  auth   │ │ chat /  │ │ prompts │ │tools │ │  eval   │ │
        │ │  users  │ │  ask    │ │         │ │      │ │         │ │
        │ │  kb /   │ │ stream  │ │         │ │      │ │         │ │
        │ │ docs    │ │         │ │         │ │      │ │         │ │
        │ └────┬────┘ └────┬────┘ └────┬────┘ └──┬───┘ └────┬────┘ │
        │      │           │           │         │          │      │
        │      ▼           ▼           ▼         ▼          ▼      │
        │ ┌────────────────────── Service 层 ────────────────────┐ │
        │ │ ChatService  AgentService  PromptService  EvalService│ │
        │ │ RetrievalService   LLMRouter   ToolRegistry          │ │
        │ │ EmbeddingService   Reranker    Chunking              │ │
        │ │ VectorStore（抽象）── PgVectorStore / Sharded         │ │
        │ └──────────────────────────────────────────────────────┘ │
        └──────────────────┬───────────────────┬──────────────────┘
                           │                   │
              ┌────────────┴───┐       ┌───────┴────────┐
              ▼                ▼       ▼                ▼
       ┌────────────┐  ┌────────────┐ ┌──────┐  ┌──────────────┐
       │ PostgreSQL │  │  pgvector  │ │Redis │  │ LLM / Embed   │
       │  (主表/审计) │  │ (chunks)  │ │(限流) │  │ Provider      │
       └────────────┘  └────────────┘ └──────┘  └──────────────┘
```

## 2. 模块边界

### 2.1 API 层（`app/api/routes/`）
- 12 个路由模块，按功能拆分：用户 / 鉴权 / 知识库 / 文档 / Chunk / Chat / Prompts / Tools / Eval / 私有调试。
- 路由只做：请求校验、归属校验、限流、调用 Service、组织响应；不写业务规则。
- 鉴权统一走 `app/api/deps.py` 的 `CurrentUser` 依赖；DB session 走 `SessionDep`。

### 2.2 Service 层（`app/services/`）
| Service | 职责 |
| --- | --- |
| `ChatService` | RAG 主问答（非流式 + SSE 流式）；编排 Retrieval → Prompt → LLM → 落库 |
| `AgentService` | ReAct 工具循环；通过 `ToolRegistry` 调度工具；步数防护 |
| `RetrievalService` | 向量召回 + BM25 召回 + RRF 融合 + Rerank → top-k |
| `EmbeddingService` | Embedding 抽象（mock / openai） |
| `Reranker` | Cross-Encoder 重排，依赖缺失时降级"分数透传" |
| `Chunking` | 三种切分策略（fixed / markdown / paragraph） |
| `PromptService` | Prompt 版本化、激活、渲染 |
| `ToolRegistry` | 工具注册中心，导出 OpenAI function-calling schema |
| `LLMRouter` | 多 Provider 调度（轮询 / 最少在途 / 成本路由 / 熔断 / 限速 / fallback） |
| `VectorStore` | 抽象接口，落地 `PgVectorStore` / `ShardedVectorStore` |
| `ShardRouter` | 按 `kb_id` 哈希分片；定向路由 + scatter-gather |
| `EvalService` | RAG 指标计算（Hit@k / MRR / Recall@k / 上下文精度 / 答案相关性 / 忠实度） |
| `Observability` | Langfuse Trace 包装 |

### 2.3 数据层（`app/models.py`）
所有表用 SQLModel 声明，含 pgvector / tsvector 列。

| 表 | 关键字段 | 备注 |
| --- | --- | --- |
| `user` / `item` | 模板自带 | JWT 鉴权 |
| `knowledgebase` | owner_id | 知识库聚合根 |
| `document` | knowledge_base_id, processing_status | 异步处理状态机：pending → processing → ready / failed |
| `documentchunk` | embedding `vector(N)`, tsv `tsvector`, kb_id（冗余） | 检索单元 |
| `chatsession` | user_id, knowledge_base_id | 会话 |
| `chatmessage` | role, content, prompt_version | 历史消息 |
| `retrievallog` | retrieved_chunk_ids, scores(jsonb), is_refused, latency_ms | 检索可解释性 |
| `tokenusage` | model, prompt/completion tokens, estimated_cost numeric(12,6) | 计费审计 |
| `promptconfig` | name, version, system/retrieval/answer 模板, is_active | Prompt 版本化 |

## 3. 关键调用链路

### 3.1 文档摄取
```
POST /documents/ ── upload file ──▶ 写入 storage_path ──▶ commit Document(status=pending)
                                                                  │
                                                                  ▼
                                       异步 worker (document_tasks.process_document)
                                                                  │
   提取文本 (txt/md/docx/pdf) ──▶ Chunking 切分 ──▶ EmbeddingService.batch_embed
                                                                  │
                                       INSERT document_chunks (embedding, tsv 由触发器同步)
                                                                  │
                                                                  ▼
                                       Document.status = ready / chunk_count = N
```

### 3.2 RAG 流式问答
```
POST /chat/sessions/{id}/ask/stream
    │
    ├─▶ 归属校验（session.user_id == current_user.id）
    ├─▶ Redis 限流（按用户 RPM）
    ├─▶ ChatService.ask_stream(query)
    │     │
    │     ├─▶ RetrievalService.hybrid_search(query, top_k)
    │     │     ├─ EmbeddingService.embed(query)
    │     │     ├─ vector_search   (pgvector 余弦)
    │     │     ├─ bm25_search     (tsvector ts_rank, ILIKE 降级)
    │     │     ├─ RRF 融合
    │     │     └─ Reranker 重排 → top_k chunks
    │     │
    │     ├─▶ 拒答检查（max(score) < REFUSAL_THRESHOLD → emit refusal）
    │     │
    │     ├─▶ PromptService.render(active_version, query, chunks)
    │     │
    │     ├─▶ LLMRouter.stream_chat(messages)
    │     │     ├─ 选择 Provider（策略 + 熔断 + 限速余量）
    │     │     ├─ 失败 → fallback 链
    │     │     └─ yield token chunks
    │     │
    │     └─▶ 落库 chat_message / retrieval_log / token_usage
    │
    └─▶ SSE 编码：
        event: retrieval  → {citations, scores}
        event: token      → {delta}
        event: token      → {delta}
        ...
        event: done       → {usage, trace_id}
```

### 3.3 Agent 工具循环
```
POST /chat/sessions/{id}/ask  (use_agent=true)
    │
    ├─▶ AgentService.run(query, kb_id)
    │     │
    │     │ ┌─────── ReAct 循环（最多 AGENT_MAX_STEPS 步）─────┐
    │     │ │                                                  │
    │     │ ▼                                                  │
    │     │ LLM.chat(messages, tools=ToolRegistry.list_schemas())
    │     │ │
    │     │ ├─ 返回 final answer ──▶ 退出循环
    │     │ │
    │     │ └─ 返回 tool_calls
    │     │     ├─ ToolRegistry.execute(name, args)
    │     │     ├─ 把 tool_result 作为 role=tool 消息追加
    │     │     └─ 进入下一轮
    │     │
    │     └─▶ {answer, steps[], citations[], usage}
    │
    └─▶ 适配为 AskResponse（与普通 RAG 同形）
```

## 4. 降级与容错

| 失败点 | 降级方式 |
| --- | --- |
| 缺少 OpenAI key / Anthropic key | `LLM_PROVIDER=mock`：内置 MockProvider，链路可跑 |
| 缺少 sentence-transformers | Reranker 降级"分数透传"（不重排） |
| 缺少 `pypdf` | 仅支持 txt/md/docx，文档处理状态置 failed 但不阻塞其他文档 |
| BM25 全文检索不可用（无 tsv 索引） | 降级 ILIKE 模糊匹配 |
| Provider 错误率 / 延迟越线 | 熔断 OPEN → 摘除流量 → HALF_OPEN 探活 → CLOSED 恢复 |
| 主 Provider 调用失败 | 进入 fallback 链（次选模型，流式连接前生效） |

## 5. 安全

- 除登录注册外所有路由默认鉴权（JWT，Argon2/bcrypt 密码哈希）。
- 资源（KB / Document / Session）按 `owner_id` / `user_id` 严格隔离。
- 原始 SQL 一律 `sqlalchemy.text` 参数化，禁止字符串拼接。
- `.env` 不入库；`SECRET_KEY` / `POSTGRES_PASSWORD` 在非 local 环境强制要求修改默认值（`config.py` 启动校验）。
- CORS 白名单可配置，默认仅本地开发口。

## 6. 演进路径

| 阶段 | 形态 | 关键变化 |
| --- | --- | --- |
| v1（当前） | 单机 pgvector + 单 LLM Provider | 可上线小流量场景 |
| v2 规模化 | LLMRouter 多 Provider + ShardedVectorStore 分片 | 业务层不变，仅替换 `VectorStore` 实现、注入多 Provider 池 |
| v3 多租户 | 行级 RLS + 物理隔离 KB | 在 v2 基础上增加 PG Row Security 策略 |

更多规模化细节见 [`scaling.md`](scaling.md)。
