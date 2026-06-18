# API 参考

> 完整 OpenAPI 见 http://localhost:8000/docs ；本文档列核心 AI 相关端点的字段。所有路径前缀 `/api/v1`。

## 鉴权

```
POST /login/access-token
  Content-Type: application/x-www-form-urlencoded
  Body: username=<email>&password=<pwd>
  Resp: { access_token, token_type }
```

之后所有请求带 `Authorization: Bearer <token>`。

## 知识库 / 文档

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/knowledge-bases/` | 列表（分页） |
| POST | `/knowledge-bases/` | 创建 `{name, description?}` |
| GET | `/knowledge-bases/{id}` | 详情 |
| PATCH | `/knowledge-bases/{id}` | 更新 |
| DELETE | `/knowledge-bases/{id}` | 删除（级联） |
| POST | `/documents/upload` | multipart：`knowledge_base_id` + `file`，异步处理 |
| GET | `/documents/?knowledge_base_id=...` | 列表 |
| GET | `/documents/{id}` | 详情（含 `processing_status` / `chunk_count`） |
| DELETE | `/documents/{id}` | 删除（级联 chunks） |
| GET | `/document-chunks/?document_id=...` | 列出文档的切片 |

`Document.processing_status`：`pending` → `processing` → `ready` / `failed`，失败原因写入 `error_message`。

## 会话与问答

### 创建会话

```
POST /chat/sessions
{
  "knowledge_base_id": "<uuid>",
  "title": "demo"
}
→ 200 ChatSessionPublic
```

### 列出 / 历史

```
GET /chat/sessions?skip=0&limit=100
GET /chat/sessions/{id}/messages
```

### RAG 主问答（非流式）

```
POST /chat/sessions/{id}/ask
{
  "query": "...",
  "top_k": 4,                  // 1..20
  "use_agent": false,          // true → 走 Agent ReAct 工具循环
  "prompt_version": null       // null → 用当前 active 的 Prompt
}

→ 200
{
  "answer": "...",
  "is_refused": false,
  "citations": [
    { "chunk_id": "<uuid>", "document": "spec.md", "chunk_index": 3, "preview": "..." }
  ],
  "retrieval_count": 4,
  "usage": { "prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200, "estimated_cost": 0.0001 },
  "trace_id": null
}
```

错误码：

| Code | 含义 |
| --- | --- |
| 401 | 未登录 / token 失效 |
| 403 | 会话不属于当前用户 |
| 404 | 会话或知识库不存在 |
| 429 | Redis 限流（按用户 RPM 超限） |

### SSE 流式问答

```
POST /chat/sessions/{id}/ask/stream
Body 同上
Content-Type: text/event-stream
```

事件序列：

```
event: retrieval
data: {"citations":[...], "scores":{"vector":[...], "bm25":[...], "rerank":[...]}}

event: token
data: {"delta":"..."}

event: token
data: {"delta":"..."}
...

event: done
data: {"usage":{...}, "trace_id":"..."}
```

> 客户端注意：浏览器 `EventSource` 不支持 POST，前端用 `fetch` + `ReadableStream` 解析。

### 拒答

当 `max(top_k 分数) < REFUSAL_THRESHOLD`：

```json
{
  "answer": "我无法在当前知识库中找到可靠依据回答这个问题。",
  "is_refused": true,
  "citations": [],
  ...
}
```

流式场景下会先 emit 一个 `refusal` 事件再 `done`。

## Prompt 版本化

```
GET  /prompts/                列表
POST /prompts/                新建
   { "name": "kb-qa", "version": "v2",
     "system_prompt": "...", "retrieval_template": "...", "answer_template": "..." }
POST /prompts/{id}/activate   激活（同 name 下其他自动 deactivate）
```

每条 chat_message 记录 `prompt_version`，便于 A/B 与效果归因。

## 工具 / Agent

```
GET /tools/
→ {
  "tools": [
    { "type": "function", "function": { "name": "search_knowledge_base", "description": "...", "parameters": {...} } },
    ...
  ]
}

POST /tools/run
{ "tool_name": "search_knowledge_base", "args": { "query": "...", "kb_id": "<uuid>", "k": 4 } }
→ { "result": ... }
```

走 Agent 循环：在 `/chat/sessions/{id}/ask` 加 `"use_agent": true` 即可。

内置工具：

| 名称 | 用途 |
| --- | --- |
| `search_knowledge_base` | 在指定知识库做混合检索 |
| `get_document_meta` | 拉取文档元信息（标题 / 创建时间 / chunk 数） |
| `kb_info` | 知识库摘要（文档数 / 总 chunk 数 / 最近更新） |
| `mcp_tool` | MCP Bridge：调用外部 MCP Server 暴露的工具 |

## 评估

```
POST /eval/run
{
  "dataset": [                       // 可选，缺省用内置 sample_golden.json
    { "query": "...", "expected_chunk_ids": ["<uuid>", ...] }
  ],
  "top_k": 4,                        // 可选覆盖
  "prompt_version": "v2",            // 可选覆盖
  "with_generation": true            // 是否跑生成（关掉只测召回）
}

→ 200
{
  "config": { ... },
  "num_items": 30,
  "hit_rate_at_k": 0.83,
  "mrr": 0.61,
  "recall_at_k": 0.78,
  "context_precision": 0.74,
  "avg_relevance": 0.69,
  "faithfulness": 0.81,
  "avg_latency_ms": 412.3,
  "estimated_cost": 0.0123,
  "refusal_rate": 0.07
}
```

CI 守门：`python backend/scripts/eval_ci.py --hit-at-k 0.7 --mrr 0.5` 任一阈值未达即非零退出。

## 限流

按用户每分钟令牌桶（Redis），命中 `429`。配置：`RATE_LIMIT_PER_MINUTE`（默认 30）。

## 字段约定

- 时间统一 ISO-8601 UTC（DB 存 `timestamptz`）。
- ID 一律 UUID v4 字符串。
- `usage.estimated_cost` 单位是美元，按 provider × model 单价表估算。
