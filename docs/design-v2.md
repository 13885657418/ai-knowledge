# 企业知识库 AI 助手后端 · 详细设计文档（v2）

> 基于 fastapi/full-stack-fastapi-template 的 AI 应用后端项目方案
> 技术主线：FastAPI / Python / SQLModel / PostgreSQL(pgvector) / Redis / Docker / RAG / Agent / MCP / Langfuse
> 运行目标：smoke-test + local-full-run，时间预算 10 天

---

## 0. 版本说明（相对 v1 的变更）

v2 在 v1「工程闭环」的基础上把 AI 能力从演示级升级到工程级，目标是让项目落在 AI 应用开发（后端）方向，而不是停留在"会调模型接口的普通后端"。变更如下：

| 维度 | v1（原方案） | v2（本方案） |
| --- | --- | --- |
| 检索 | 关键词/文本包含「伪检索」 | 真实 embedding + pgvector 向量检索 + BM25 混合检索 + rerank 重排 |
| 数据库 | MySQL，embedding 存 json | PostgreSQL + pgvector，向量原生检索 |
| 生成 | 一次性返回 | SSE 流式输出 + 结构化输出 |
| 幻觉控制 | 无 | 相似度阈值拒答 + Prompt 强约束 + citation 溯源 |
| Prompt | 写死在代码 | 可版本化、可热切换的 Prompt 配置 |
| Agent | 空 ToolRegistry + 玩具工具 | function calling 驱动的 ReAct 工具循环 + 真实 MCP 接入 |
| 可观测 | 仅 retrieval_log | retrieval_log + token/成本统计 + Langfuse 全链路追踪 |
| 文档处理 | 同步 | 后台异步任务 + 状态机 |
| 评估 | 无 | RAG 评估脚本（召回命中率 / 答案相关性） |

---

## 1. 项目概述

以 fastapi/full-stack-fastapi-template 为工程底座，构建面向企业内部知识库场景的 AI 问答后端。系统覆盖「文档处理 → 检索优化 → 生成控制 → Agent 扩展 → 工程化」完整链路，10 天内交付一个可本地运行、可讲清、可投递、可面试的 AI 应用后端。

### 1.1 建设目标

- 完成业务感真实的 AI 应用后端，满足「AI 应用开发（后端）」JD 的面试表达需求。
- 实现真 RAG 闭环：上传 → 解析 → 多策略切分 → 向量化 → 混合检索 → rerank → 拒答判定 → Prompt 组装 → 流式生成 → citation 溯源 → 落库。
- 通过 Prompt 版本管理、token 成本统计、Langfuse 追踪体现 AI 应用工程化能力。
- 通过 function calling Agent 循环 + MCP 接入体现复杂任务编排的演进能力。

### 1.2 非目标范围

- 不追求生产级高并发、分布式向量库与复杂任务调度。
- 不在首版实现复杂前端界面。
- 不追求论文级 RAG 指标，以"工程闭环 + 可讲 + 可评估"为优先。

---

## 2. 用户画像与场景

### 2.1 用户画像
- 项目操作者：后端候选人，已具备 FastAPI / SQLModel / RAG / Agent 基础，缺少完整 AI 项目经验。
- 终端用户：企业内部员工，需基于公司文档（制度、手册、FAQ、流程）做自然语言问答。
- 面试官关注点：RAG 检索质量、幻觉控制、Agent 设计、AI 工程化（成本/可观测/评估）、后端工程结构。

### 2.2 业务场景
- 企业沉淀大量制度文档、产品手册、FAQ、流程说明。
- 员工用自然语言提问，系统返回有引用来源、可溯源、可信的答案。
- 系统记录问答历史、检索证据、成本与延迟，便于复盘、评估与优化。

---

## 3. 总体架构设计

### 3.1 架构分层

```
┌─────────────────────────────────────────────────────────┐
│ API Layer        REST + SSE 流式接口、鉴权、参数校验、限流  │
├─────────────────────────────────────────────────────────┤
│ Schema Layer     Pydantic/SQLModel 请求响应模型、结构化输出 │
├─────────────────────────────────────────────────────────┤
│ Service Layer    文档处理 / 检索 / 生成 / Agent / 日志       │
│   ├─ DocumentService   解析 + 多策略切分 + 异步向量化        │
│   ├─ RetrievalService  向量检索 + BM25 + 融合 + rerank      │
│   ├─ LLMService        Provider 抽象 + 流式 + token 统计    │
│   ├─ PromptService     Prompt 版本管理 + 模板渲染            │
│   ├─ ChatService       会话 + 多轮上下文 + 拒答判定          │
│   └─ AgentService      function calling 循环 + ToolRegistry │
├─────────────────────────────────────────────────────────┤
│ Data Layer       SQLModel ORM + pgvector 向量存储           │
├─────────────────────────────────────────────────────────┤
│ Infra            PostgreSQL(pgvector) / Redis / Langfuse    │
└─────────────────────────────────────────────────────────┘
        ↑ Tool / MCP Extension（被 AgentService 调用）
```

### 3.2 关键数据流

1. 文档处理（异步）：上传 → 落 documents(pending) → 后台任务解析+切分+embedding → 写 chunks → 状态 ready。
2. RAG 问答（流式）：query → (多轮 query 改写) → 混合检索 → rerank → 阈值拒答判定 → Prompt 渲染 → LLM 流式生成 → SSE 返回 → 落 messages + retrieval_log + token 统计 → Langfuse 上报。
3. Agent 扩展：query 进入 AgentService → 模型经 function calling 自主决定调用工具（含 MCP）→ 工具结果回灌 → 继续推理直至产出答案。

---

## 4. 功能设计

### 4.1 鉴权与用户管理
- 复用模板用户体系与鉴权。知识库、文档、会话均与 user 绑定，用户只能访问自有资源。
- 新增按用户限流（Redis 计数），保护 LLM 调用成本。

### 4.2 知识库管理
- 创建 / 列表（按用户过滤）/ 详情（含文档数、最近更新）/ 删除（软删除 + 级联清理 chunk）。

### 4.3 文档管理与处理（异步）
- 支持上传 txt / md / pdf（首版保证 txt/md 稳定，pdf 增强）。
- 上传仅落元数据（status=pending），实际解析/切分/向量化由后台任务执行，前端轮询 status。
- 多策略切分：固定窗口 + overlap（默认）/ 按 Markdown 标题结构 / 按段落语义。策略可选。
- 每个 chunk 生成真实 embedding 并写入 pgvector 向量列。

### 4.4 RAG 问答（核心）
- 多轮 query 改写：结合历史消息，把"它的流程呢"改写为完整可检索问题。
- 混合检索：向量检索（pgvector 余弦）+ BM25 关键词检索，分数融合（RRF / 加权）。
- rerank 重排：对融合后的候选用 cross-encoder（或轻量 reranker）重排，取最终 top-k。
- 拒答机制：最高相关性低于阈值时，直接返回"知识库中未找到相关内容"，不调用大模型硬编答案。
- 生成约束：Prompt 强制"仅依据检索片段作答 + 标注引用"，降低幻觉。
- 流式输出：通过 SSE 逐 token 返回 answer。
- 响应含 answer、citations（含文档名+段落定位）、retrieval_count、token 与成本。

### 4.5 会话管理与上下文
- 创建会话、按 session 存 user/assistant 消息、查询历史。
- 上下文管理：多轮历史过长时做截断或摘要压缩后再进 Prompt。

### 4.6 Prompt 工程与版本管理
- system / 检索 / 回答模板存 prompt_configs，支持版本化、启停、热切换。
- 问答时记录所用 prompt 版本，便于 A/B 与效果归因。

### 4.7 可解释性与可观测
- retrieval_log 记录 query、改写后 query、top_k、召回 chunk、各阶段分数、延迟。
- token/成本统计：prompt_tokens / completion_tokens / 估算成本，按会话与用户聚合。
- citation 溯源：定位答案来源到具体文档与 chunk_index。
- Langfuse 追踪：检索 → Prompt → 生成全链路 trace 可视化。

### 4.8 Agent / MCP 扩展
- ReAct 式工具循环：基于 LLM function calling，模型自主决定是否/调用哪个工具，工具结果回灌再推理。
- ToolRegistry：注册本地工具（查文档元数据、查知识库信息）。
- MCP 接入：对接真实 MCP server（如 filesystem / fetch），工具以统一 schema 暴露给模型。

### 4.9 RAG 评估（可选但加分）
- 评估脚本：基于人工标注的 (query, 期望文档) 计算召回命中率（Hit@k）与答案相关性（可用 LLM-as-judge）。

---

## 5. 技术选型

| 领域 | 选型 | 原因 |
| --- | --- | --- |
| Web 框架 | FastAPI | 类型安全、自动文档、原生支持异步与 SSE 流式 |
| 语言 | Python | AI / RAG / Agent 生态成熟 |
| 数据校验 | Pydantic / SQLModel | 请求响应边界清晰，支持结构化输出 |
| ORM | SQLModel(SQLAlchemy) | 与模板一致，便于讲数据建模 |
| 数据库 | PostgreSQL + pgvector | 关系数据 + 原生向量检索一库搞定，省去额外向量库运维 |
| 向量检索 | pgvector（余弦/内积） | 真向量相似度，支持 ivfflat/hnsw 索引 |
| 关键词检索 | BM25（PG 全文检索 / rank_bm25） | 与向量做混合检索 |
| rerank | cross-encoder（bge-reranker / sentence-transformers） | 提升最终召回相关性 |
| Embedding | OpenAI text-embedding-3-small 或开源 bge-small-zh / m3e | 中文场景优先开源，省成本、可离线 |
| LLM | OpenAI / Claude（Provider 抽象 + fallback） | 解耦供应商，支持切换与容灾 |
| 缓存/限流 | Redis | 语义/结果缓存、按用户限流、会话上下文缓存 |
| 异步处理 | FastAPI BackgroundTasks（或 Celery） | 文档解析/向量化不阻塞请求 |
| 可观测 | Langfuse | LLM 链路 trace、成本与延迟可视化 |
| 部署 | Docker / Docker Compose | 一键起 app + pg + redis，本地可跑 |

> 选型取舍：v1 用 MySQL + Python 循环算相似度，数据量稍大就不可用，也讲不出"向量库"亮点。v2 换 PostgreSQL+pgvector 是性价比最高的升级，一个库同时承载业务数据与向量检索，无需引入独立向量数据库就能拿到工程级检索能力。

---

## 6. 数据库设计

复用 users（模板已有）。新增/调整如下（PostgreSQL）。

### 6.1 knowledge_bases
| 字段 | 类型 | 说明 | 约束 |
| --- | --- | --- | --- |
| id | uuid | 主键 | PK |
| owner_id | uuid | 所属用户 | FK→users.id, cascade |
| name | varchar(128) | 名称 | not null |
| description | text | 说明 | nullable |
| is_deleted | bool | 软删除 | default false |
| created_at / updated_at | timestamptz | 时间 | not null |

### 6.2 documents
| 字段 | 类型 | 说明 | 约束 |
| --- | --- | --- | --- |
| id | uuid | 主键 | PK |
| knowledge_base_id | uuid | 所属知识库 | FK |
| filename | varchar(255) | 原始文件名 | not null |
| file_type | varchar(32) | 类型 | not null |
| storage_path | varchar(255) | 存储路径 | not null |
| chunk_strategy | varchar(32) | 切分策略 | default 'fixed' |
| status | varchar(32) | pending/processing/ready/failed | index |
| error_message | text | 失败原因 | nullable |
| chunk_count | int | chunk 数 | default 0 |
| created_at | timestamptz | 时间 | not null |

### 6.3 document_chunks
| 字段 | 类型 | 说明 | 约束 |
| --- | --- | --- | --- |
| id | uuid | 主键 | PK |
| document_id | uuid | 所属文档 | FK |
| knowledge_base_id | uuid | 冗余便于检索过滤 | FK, index |
| chunk_index | int | 顺序号 | not null |
| content | text | 文本内容 | not null |
| token_count | int | token 数 | nullable |
| embedding | vector(N) | pgvector 向量列（N=embedding 维度） | nullable, ivfflat/hnsw index |
| tsv | tsvector | BM25/全文检索列 | index |
| created_at | timestamptz | 时间 | not null |

### 6.4 chat_sessions
| 字段 | 类型 | 说明 | 约束 |
| --- | --- | --- | --- |
| id | uuid | 主键 | PK |
| user_id / knowledge_base_id | uuid | 关联 | FK |
| title | varchar(255) | 标题 | nullable |
| created_at / updated_at | timestamptz | 时间 | not null |

### 6.5 chat_messages
| 字段 | 类型 | 说明 | 约束 |
| --- | --- | --- | --- |
| id | uuid | 主键 | PK |
| session_id | uuid | 所属会话 | FK |
| role | varchar(32) | user/assistant/system/tool | not null |
| content | text | 内容 | not null |
| model_name | varchar(128) | 模型 | nullable |
| prompt_version | varchar(64) | 所用 prompt 版本 | nullable |
| created_at | timestamptz | 时间 | not null |

### 6.6 retrieval_logs（增强）
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | uuid | 主键 |
| session_id | uuid | 所属会话 |
| query_text / rewritten_query | text | 原始/改写后问题 |
| retrieved_chunk_ids | jsonb | 召回 chunk id |
| scores | jsonb | 各阶段分数（向量/BM25/rerank） |
| top_k | int | 召回数 |
| is_refused | bool | 是否触发拒答 |
| latency_ms | int | 总耗时 |
| created_at | timestamptz | 时间 |

### 6.7 token_usages（新增）
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | uuid | 主键 |
| session_id / user_id | uuid | 归属 |
| model_name | varchar | 模型 |
| prompt_tokens / completion_tokens / total_tokens | int | token 统计 |
| estimated_cost | numeric | 估算成本 |
| created_at | timestamptz | 时间 |

### 6.8 prompt_configs（启用）
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | uuid | 主键 |
| name / version | varchar | 模板名 + 版本 |
| system_prompt / retrieval_template / answer_template | text | 模板内容 |
| is_active | bool | 是否启用（同 name 仅一个 active） |
| created_at | timestamptz | 时间 |

---

## 7. 核心接口设计

> 前缀统一 `/api/v1`。鉴权复用模板 OAuth2/JWT，资源按 user 过滤。

### 7.1 知识库
- `POST /knowledge-bases` 创建 → `{id, name}`
- `GET /knowledge-bases` 列表 → `[{id, name, document_count}]`
- `GET /knowledge-bases/{id}` 详情
- `DELETE /knowledge-bases/{id}` 软删除

### 7.2 文档
- `POST /documents/upload` multipart(file + knowledge_base_id + chunk_strategy?) → `{id, status:"pending"}`
- `POST /documents/{id}/process` 触发后台处理（或上传即自动触发）→ `{id, status:"processing"}`
- `GET /documents/{id}` 查处理状态 → `{id, status:"ready", chunk_count:26}`
- `GET /documents?knowledge_base_id=` 列表

### 7.3 会话与问答
- `POST /chat/sessions` 创建 → `{id, title}`
- `GET /chat/sessions/{id}/messages` 历史
- `POST /chat/sessions/{id}/ask` RAG 主问答（非流式）
  请求：`{query, top_k:4, use_agent:false, prompt_version?}`
  响应：
  ```json
  {
    "answer": "...",
    "is_refused": false,
    "citations": [{"chunk_id":"...","document":"policy.md","chunk_index":3,"preview":"..."}],
    "retrieval_count": 4,
    "usage": {"prompt_tokens":1200,"completion_tokens":180,"estimated_cost":0.0012},
    "trace_id": "langfuse-xxx"
  }
  ```
- `POST /chat/sessions/{id}/ask/stream` SSE 流式问答
  事件序列：`event: retrieval`（召回元数据）→ 多个 `event: token`（增量文本）→ `event: done`（citations + usage）。

### 7.4 Prompt 管理
- `GET /prompts` / `POST /prompts` / `POST /prompts/{id}/activate`

### 7.5 Agent / 工具（可选）
- `POST /tools/run` `{tool_name, args}` → `{result}`（直接调用单个工具）
- Agent 循环不单独开接口，由 `ask` 的 `use_agent=true` 触发。

### 7.6 评估（可选）
- `POST /eval/run` 输入标注集 → 返回 `{hit_rate@k, avg_relevance}`

---

## 8. 核心流程设计

### 8.1 文档处理流程（异步 + 状态机）
1. 上传 → 创建 documents 记录，status=pending，文件落本地 volume。
2. 后台任务拉起，status=processing。
3. 按 chunk_strategy 抽取文本并切分（固定窗口+overlap / Markdown 标题 / 段落）。
4. 批量调用 embedding 模型生成向量，写入 document_chunks（含 embedding 向量列 + tsv 全文列）。
5. 成功 → status=ready，更新 chunk_count；失败 → status=failed，记录 error_message。

### 8.2 RAG 问答流程（核心）
1. 校验 session 与 knowledge_base 权限，命中限流则拒绝。
2. query 改写：取最近 N 轮历史，让 LLM 把指代/省略补全为完整问题（多轮才触发）。
3. 混合检索：
   - 向量检索：query embedding 与 pgvector 列做余弦 top-m。
   - BM25：tsv 列关键词召回 top-m。
   - RRF / 加权融合得到候选集。
4. rerank：cross-encoder 对候选重排，取最终 top-k。
5. 拒答判定：若最高分 < 阈值 → is_refused=true，直接返回"未找到相关内容"，跳过 LLM。
6. Prompt 渲染：取 active prompt_config，注入检索片段 + 历史上下文（必要时摘要压缩）。
7. 生成：LLMService 调用模型（流式/非流式），强约束"仅依据片段作答 + 标注引用"。
8. 落库与上报：写 user/assistant message、retrieval_log、token_usages，Langfuse 上报 trace。
9. 返回 answer + citations（溯源到文档 + chunk_index）+ usage。

### 8.3 Agent 工具循环（use_agent=true）
1. 将本地工具 + MCP 工具的 JSON Schema 作为 tools 传给模型。
2. 模型返回 tool_calls → AgentService 执行（含知识库检索工具、文档元数据工具、MCP 工具）。
3. 工具结果作为 role=tool 消息回灌。
4. 循环直至模型产出最终答案或达到最大步数，记录每步到 retrieval_log/Langfuse。

### 8.4 多模型容灾
- LLMService 抽象 Provider，主 Provider 异常时按配置 fallback 到备用模型，记录切换事件。

---

## 9. 目录结构设计

```
app/
  api/
    routes/
      auth.py  users.py
      knowledge_bases.py  documents.py
      chat.py            # ask + ask/stream
      prompts.py  tools.py  eval.py
  core/
    config.py  security.py  rate_limit.py
  db/
    base.py  session.py
  models/                # SQLModel（含 vector 列）
    knowledge_base.py document.py document_chunk.py
    chat_session.py chat_message.py retrieval_log.py
    token_usage.py prompt_config.py
  schemas/
    knowledge_base.py document.py chat.py tool.py eval.py
  services/
    document_service.py        # 解析 + 多策略切分 + 异步向量化
    chunking.py                # 切分策略
    embedding_service.py       # embedding 抽象
    retrieval_service.py       # 向量 + BM25 + 融合 + rerank
    reranker.py
    llm_service.py             # Provider 抽象 + 流式 + token 统计 + fallback
    prompt_service.py          # Prompt 版本管理与渲染
    chat_service.py            # 编排 RAG 主链路 + 拒答 + 上下文压缩
    agent_service.py           # function calling 循环
    tool_registry.py
    tools/
      base.py  kb_info_tool.py  document_meta_tool.py  mcp_tool.py
    observability.py           # Langfuse 封装
    eval_service.py            # RAG 评估
  workers/
    document_tasks.py          # 后台文档处理任务
```

> 落地策略：先按模板风格把模型/CRUD/路由跑通（方案 A），核心 RAG 链路稳定后再把复杂逻辑抽进 services/（方案 B），避免一开始过度设计目录。

---

## 10. 关键设计决策

- PostgreSQL+pgvector 而非 MySQL+Python 相似度：一库承载业务+向量，得到真向量检索且零额外运维，是 AI 含量与工程成本的最佳平衡。
- 混合检索 + rerank 而非纯向量：纯向量易漏关键词精确匹配，混合+重排是工业界 RAG 的常规做法，召回质量更好且面试可讲。
- 拒答机制优先于"答得多"：企业知识库场景下，"不知道就说不知道"比硬编答案有价值，是抑制幻觉的关键设计。
- 异步文档处理：解析+向量化耗时，后台任务+状态机避免阻塞，体现真实工程考量。
- Agent 用 function calling 真循环而非开关：让模型自主决策调用工具才算 Agent，写死的 if-else 不算。
- Prompt 版本化：把 Prompt 当作可迭代的"配置/代码"管理，支撑 A/B 与效果归因。

---

## 11. 非功能设计

### 11.1 安全
- 接口鉴权 + 资源按 user 过滤；上传文件类型/大小校验。
- query 与工具输入做基础校验与（可选）敏感内容过滤，避免恶意内容进入工具层。
- 网络暴露提示：默认接口需鉴权，不创建无鉴权的对外服务。

### 11.2 成本与稳定
- 按用户限流（Redis）；token/成本统计与聚合；可选语义缓存命中相同/相似 query。
- LLM Provider fallback，降低单点不可用风险。

### 11.3 可观测
- retrieval_log + token_usages + Langfuse trace；关键流程结构化日志，记录各阶段延迟与召回分数。

### 11.4 可维护
- 路由/service/model/schema 分层；业务逻辑不写进路由；外部 LLM/embedding/reranker 全部经 service 抽象。

---

## 12. Docker 与运行方案

- smoke-test：起 app + postgres(pgvector) + redis，验证登录、建库、上传、问答接口走通。
- local-full-run：完整跑通上传→异步处理→混合检索→rerank→流式问答→落库→Langfuse 追踪。
- Docker Compose 管理 app / postgres / redis（Langfuse 可选另起），文档文件挂本地 volume。
- 提供 `.env.example`（模型 key、embedding 维度、阈值、top_k、限流配置等）。

---

## 13. 测试设计

- 单元：切分策略、融合算法（RRF）、拒答阈值判定、Prompt 渲染、token 估算、ToolRegistry。
- 接口：建库、上传、异步处理状态流转、ask、ask/stream（SSE）。
- 联调：上传→处理→问答返回 citation 全链路；Agent 工具循环链路。
- 评估：用标注集跑 eval，输出 Hit@k 与答案相关性。
- 验收：接口成功率、链路闭环、日志/成本落库、SSE 正常、Docker 可一键运行。

---

## 14. 10 天实施计划

| 时间 | 目标 |
| --- | --- |
| Day 1 | 跑通模板，切换 PostgreSQL+pgvector，梳理结构与配置 |
| Day 2 | 业务建模、ER 图、API 草图，接入 embedding 模型 |
| Day 3 | 实现 kb/document/chunk(含 vector 列)/session/message/log 模型与迁移 |
| Day 4 | 知识库 + 文档管理接口，异步文档处理 + 多策略切分 + 向量化 |
| Day 5 | 混合检索（向量+BM25）+ 融合 |
| Day 6 | rerank 重排 + 拒答机制 + Prompt 版本管理 |
| Day 7 | RAG 主问答接口 + SSE 流式输出 + citation 溯源 |
| Day 8 | retrieval_log + token/成本统计 + Langfuse 追踪，多轮 query 改写与上下文压缩 |
| Day 9 | Agent function calling 循环 + ToolRegistry + MCP 接入，限流 |
| Day 10 | RAG 评估脚本，README、接口说明、简历表述、面试问答提纲，Docker 收尾 |

> 取舍优先级：必做（真 embedding+向量检索+拒答+Prompt 版本）> 强烈建议（流式+混合检索+rerank+token 统计）> 亮点（Agent 循环+Langfuse+异步）> 点睛（MCP+评估）。时间紧时从后往前砍，已完成的才写进简历。

---

## 15. 风险与应对

| 风险 | 应对 |
| --- | --- |
| pgvector/迁移踩坑 | Day1 优先切库并跑通最小向量检索 demo |
| embedding/LLM 接口不稳定 | Provider 抽象 + mock + fallback |
| rerank 模型加载重 | 用轻量 reranker 或可关闭，先保混合检索闭环 |
| Agent 循环失控 | 设最大步数 + 超时 + 工具白名单 |
| MCP 接入成本高 | 作为点睛项后置，先用本地工具占位 |
| 10 天范围失控 | 严守优先级，从"点睛"往回砍，不砍必做项 |

---

## 16. 面试价值总结

- 检索深度：向量 + BM25 混合检索 + rerank + 拒答，能讲清"为什么这样召回更准、如何抑制幻觉"。
- 生成控制：流式输出、结构化输出、Prompt 版本管理、citation 溯源。
- Agent 能力：function calling 自主工具循环 + MCP，区别于"只会调一个模型接口"。
- AI 工程化：token/成本统计、Langfuse 可观测、异步处理、限流、多模型容灾、RAG 评估。
- 后端工程：分层架构、ORM 建模、权限、Docker 一键部署。

---

## 17. 最终交付物清单

- 后端代码仓库（PostgreSQL+pgvector）
- 数据库模型与 Alembic 迁移脚本
- API 文档（含 SSE 流式说明）
- Docker Compose 本地运行方案（app + pg + redis）
- RAG 评估脚本与样例标注集
- 项目 README / 简历项目描述 / 面试问答提纲

---

## 18. 进阶演进设计（规模化方向）

> 定位说明：本节内容不在 10 天 MVP 交付范围内，是面向生产规模化的演进设计。MVP 阶段单机 pgvector + 单模型即可满足"可跑可讲"。在简历与面试中讲清楚"系统如何从单机演进到规模化"是体现架构纵深的常见加分项，面试官也常追问"数据量/并发涨 100 倍怎么办"。三个方向分别对应存储扩展、计算扩展、质量保障。

### 18.1 分布式向量检索（pgvector 分片）

问题背景：单机 pgvector 在千万级 chunk、高 QPS 下会遇到内存（HNSW 索引常驻）、单机吞吐与召回延迟瓶颈。

演进路径（由轻到重）：

1. 单机索引优化（先做）：HNSW 替代 ivfflat 提升召回质量与速度；按 `knowledge_base_id` 做分区表（PARTITION BY LIST/HASH），检索时只扫目标知识库分区，逻辑上已实现"按租户隔离的局部检索"。
2. 读写分离 + 只读副本：检索是读密集，向量写入（文档处理）是写操作。主库负责写入与 embedding 落库，多个只读副本承载检索流量，按副本数水平扩展读吞吐。
3. 分片（Sharding）：当单库容量/写入成为瓶颈时分片。
   - 分片键选择：以 `knowledge_base_id`（或 tenant_id）为分片键，保证同一知识库的 chunk 落在同一分片。向量检索天然按知识库隔离，跨分片查询极少，这是该场景分片的最大优势（不像通用搜索需要全局聚合）。
   - 分片方案：应用层分片（按 kb_id 哈希路由到不同 PG 实例），或借助 Citus（PostgreSQL 分布式扩展，原生支持分布式表 + pgvector）。
   - 路由层：新增 `ShardRouter`，根据 kb_id 计算目标分片，检索/写入定向到对应实例。
4. 跨分片聚合（仅极少数全局检索需要）：scatter-gather，并发查所有分片各取 top-k，应用层做全局归并重排（按相似度/rerank 分数）后取最终 top-k。可用 RRF 融合多分片结果。

架构示意：
```
                    ┌─────────────┐
   query ──────────▶│ ShardRouter │  按 kb_id 路由
                    └──────┬──────┘
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
       Shard-0(PG)    Shard-1(PG)    Shard-N(PG)
       kb_id%N==0     kb_id%N==1     ...
       [pgvector]     [pgvector]     [pgvector]
            └──────────────┼──────────────┘
                  全局检索时 scatter-gather + 归并重排
```

关键权衡：
- 优先分区表而非真分片。大多数企业知识库单实例足够，分区已能隔离。
- 分片引入跨实例事务、再均衡（rebalance）、运维复杂度，仅在单机确实撑不住时引入。
- 更大规模时也可以迁移到专用分布式向量库（Milvus / Qdrant 集群），但 pgvector 分区/Citus 是"够用且讲得清"的中间形态。

设计落点：抽象 `VectorStore` 接口（`add` / `search` / `delete`），单机 pgvector 与分片实现共用同一接口，业务层无感知，演进时只换实现。

### 18.2 多模型负载均衡

问题背景：单一 LLM Provider 存在限速（RPM/TPM）、偶发不可用、成本与延迟差异。v2 已有 Provider 抽象 + fallback，本节将其升级为主动的负载均衡与调度。

调度策略（可组合）：

1. 加权轮询 / 最少连接：多个 Provider（OpenAI、Claude、本地模型）与多个 API Key 组成池，按权重或当前在途请求数分发，打散单 key 限速。
2. 成本/能力分级路由（语义路由）：按任务难度选模型。简单问答走便宜小模型，复杂推理/Agent 走强模型；query 改写、标题生成等辅助任务固定走小模型。能显著降本。
3. 熔断与健康检查：每个 Provider 维护健康状态（错误率、延迟滑动窗口），超阈值熔断并暂时摘除，定时探活恢复。配合 v2 的 fallback 形成"负载均衡 + 容灾"闭环。
4. 限速感知调度：跟踪各 key 的 RPM/TPM 余量（Redis 计数），优先分发给余量充足的 key，命中限速则自动切换并退避重试。
5. 流式兼容：负载均衡在请求入口选定 Provider，选定后流式连接保持在该 Provider，不中途切换（避免 token 流断裂）；仅在建连失败时 fallback。

组件设计：
```
ChatService ──▶ LLMRouter ──┬─ ProviderPool（权重/健康/限速余量）
                            ├─ Strategy（轮询/最少连接/成本路由）
                            ├─ CircuitBreaker（熔断+探活）
                            └─ selected Provider ──▶ 流式/非流式调用
                                     │
                                     └─ 失败 ──▶ fallback 链
```
- `LLMRouter` 在 `LLMService` 之上，对业务层暴露统一 `chat()/stream()`。
- 所有调度决策（选了哪个模型、是否熔断、是否降级）记入 Langfuse 与日志，便于复盘。
- token/成本统计按 Provider 维度聚合，支撑成本路由的效果度量。

关键权衡：成本路由可能牺牲简单任务的回答质量，需用 18.3 的评估平台量化"降本是否伤害质量"，形成"路由策略 → 评估 → 调参"闭环。

### 18.3 自动化 RAG 评估平台

问题背景：RAG 系统改一个参数（切分大小、top_k、rerank 模型、Prompt 版本、检索模型）都可能影响效果，靠人工抽查不可靠也不可回归。需要可自动化、可回归、可对比的评估能力，把"调 RAG"从玄学变成数据驱动。

评估维度：
- 检索质量：Hit@k（期望文档是否在 top-k）、MRR、Recall@k、Context Precision（召回片段中相关比例）。
- 生成质量：Faithfulness（答案是否忠于检索片段，反幻觉）、Answer Relevancy（答案与问题相关性）、Answer Correctness（与标准答案一致性）。
- 工程指标：端到端延迟、token 成本、拒答率与拒答准确性。

评估数据集：
- 人工标注的 `(query, 期望命中文档/chunk, 参考答案)` 黄金集。
- LLM 合成数据集：用强模型基于已入库文档自动生成 (问题, 答案, 来源) 三元组，扩充评估集（需人工抽检质量）。

自动化架构：
```
┌──────────────┐   ┌─────────────────┐   ┌──────────────┐
│  评估数据集    │──▶│  Eval Runner     │──▶│  指标计算      │
│ (黄金集+合成)  │   │  批量跑 RAG 链路  │   │ 检索/生成/工程 │
└──────────────┘   └─────────────────┘   └──────┬───────┘
        ▲                                        ▼
        │                              ┌──────────────────┐
   配置快照(实验)                       │ 结果存储 + 报表对比 │
   切分/top_k/模型/Prompt版本           │ 实验A vs B 回归对比 │
                                       └──────────────────┘
                                               │
                                       CI 集成：PR 触发回归，
                                       指标低于基线则阻断合并
```

关键设计：
1. 实验即配置：每次评估绑定一份配置快照（embedding 模型、切分策略、top_k、rerank、prompt_version、LLM），结果可追溯到具体配置，支持 A/B 对比与历史回归。
2. LLM-as-judge：Faithfulness / Relevancy 用强模型打分，附判定理由；可对接 Ragas / TruLens 等成熟评估框架，不必全部自研。
3. CI/CD 集成：把评估作为流水线一环，改检索/Prompt 的 PR 自动跑黄金集回归，核心指标跌破基线则阻断合并，防止"优化一个 case 劣化整体"。
4. 与 Langfuse 联动：线上真实 trace 可采样进入评估集（标注后），形成"线上反馈 → 离线评估 → 优化上线"的数据飞轮。
5. 平台化（远期）：Web 看板展示各实验指标对比、按知识库/query 类型下钻、坏 case 列表，让非工程同学也能参与评估标注。

关键权衡：LLM-as-judge 有成本与稳定性问题（评分波动），需固定 judge 模型与 Prompt、多次采样取均值；合成数据集需人工抽检防止"模型给自己出题自己打高分"的偏差。

### 18.4 三方向与 MVP 的衔接（面试表达建议）

| 演进方向 | MVP 已埋的伏笔 | 一句话演进故事 |
| --- | --- | --- |
| 分布式向量检索 | `VectorStore` 接口 + 按 kb_id 检索过滤 | "单机 pgvector 先按 kb_id 分区隔离，量再大就以 kb_id 为分片键做 Citus/应用层分片，跨分片 scatter-gather 归并" |
| 多模型负载均衡 | Provider 抽象 + fallback + token 统计 | "已有 Provider 抽象和容灾，再加权重池/熔断/成本路由就是负载均衡，用评估平台验证降本不伤质量" |
| 自动化 RAG 评估 | eval_service + retrieval_log + Langfuse | "已有评估脚本和全链路日志，平台化就是加实验配置快照、A/B 对比和 CI 回归门禁" |

> 表达要点：强调这些是有接口伏笔的自然演进，而不是推倒重来。在 MVP 阶段就预留了扩展点是架构能力的核心信号。
