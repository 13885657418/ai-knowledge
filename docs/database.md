# 数据库设计

> 全部表用 SQLModel 声明在 `app/models.py`；迁移在 `app/alembic/versions/`。当前 head：`a1b2c3d4e5f6_v2_ai_tables`。

## 1. 表清单

| 分类 | 表 | 用途 |
| --- | --- | --- |
| 用户 / 模板 | `user`, `item` | 鉴权 + 模板示例 |
| 知识库 / 文档 | `knowledgebase`, `document`, `documentchunk` | RAG 数据底座 |
| 会话 / 历史 | `chatsession`, `chatmessage` | 对话状态 |
| 审计 / 评估 | `retrievallog`, `tokenusage` | 检索可解释性 + 计费 |
| 配置 | `promptconfig` | Prompt 版本化 |

## 2. 关键 ER

```
user (1) ──┬── (N) knowledgebase ──┬── (N) document ── (N) documentchunk
           │                       │
           │                       └── (N) chatsession ── (N) chatmessage
           │
           └── (N) chatsession                       ↑
                                                    │
                            retrievallog ───────────┤
                            tokenusage  ────────────┘
```

级联策略：`user → kb → document → chunk` / `user → session → message`，外键全部 `ON DELETE CASCADE`。

## 3. 关键列

### `documentchunk`（检索单元）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | uuid PK | |
| `document_id` | uuid FK→document | 归属文档 |
| `knowledge_base_id` | uuid FK→knowledgebase（**冗余**） | 检索过滤 / 分片键 |
| `owner_id` | uuid FK→user（**冗余**） | 用户隔离 |
| `chunk_index` | int | 在文档内的顺序 |
| `content` | text | 切片原文 |
| `char_count` / `token_count` | int | 长度统计 |
| `embedding` | `vector(EMBEDDING_DIM)` | 向量列，pgvector |
| `tsv` | `tsvector` | 全文检索列，触发器自动同步 |

索引：

```sql
CREATE INDEX idx_chunk_kb        ON documentchunk (knowledge_base_id);
CREATE INDEX idx_chunk_owner     ON documentchunk (owner_id);
CREATE INDEX idx_chunk_tsv       ON documentchunk USING GIN (tsv);
CREATE INDEX idx_chunk_embedding ON documentchunk USING hnsw (embedding vector_cosine_ops);
```

### `retrievallog`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | uuid FK | 关联会话（可空，调试时可不绑定） |
| `query_text` / `rewritten_query` | text | 原始问题 / 改写后 |
| `retrieved_chunk_ids` | jsonb | 召回 chunk id 列表 |
| `scores` | jsonb | `{vector:[...], bm25:[...], rrf:[...], rerank:[...]}` |
| `top_k` | int | |
| `is_refused` | bool | 是否触发拒答 |
| `latency_ms` | int | 端到端检索耗时 |

### `tokenusage`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` / `user_id` | uuid FK | 双索引便于按维度聚合 |
| `model_name` | varchar(128) | provider × model |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | int | |
| `estimated_cost` | numeric(12, 6) | 美元，按单价表估算 |

### `promptconfig`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `name` | varchar(128) | 业务名（如 `kb-qa`） |
| `version` | varchar(64) | 版本号（如 `v2`） |
| `system_prompt` | text | system 段 |
| `retrieval_template` | text | 注入检索结果的模板 |
| `answer_template` | text | 输出格式约束 |
| `is_active` | bool | 同 name 仅一条 active（业务层维护） |

## 4. 触发器 / 扩展

迁移内创建：

```sql
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector

-- tsv 同步触发器
CREATE TRIGGER documentchunk_tsv_update
BEFORE INSERT OR UPDATE OF content
ON documentchunk
FOR EACH ROW EXECUTE FUNCTION tsvector_update_trigger(tsv, 'pg_catalog.simple', content);
```

> 中文分词：默认 `pg_catalog.simple`（按空格切，对纯中文不友好）。需要更准的中文召回可：
> 1. 装 `zhparser` 扩展并改 `simple` 为 `zhparser_default`；
> 2. 或在应用层用 jieba 预分词后写入 `content`。
## 5. 迁移历史

```
0a073a23c9cd_add_knowledge_base_model.py    # 历次知识库表迭代
0f28799061ee_add_knowledge_base_model.py
3b94abeedff0_add_knowledge_base_model.py
60ea514cfc36_add_knowledge_base_model.py
6ead6fe35356_add_knowledge_base_model.py
1a31ce608336_add_cascade_delete_relationships.py
9c0a54914c78_add_max_length_for_string_varchar_.py
d98dd8ec85a3_edit_replace_id_integers_in_all_models_.py
a1b2c3d4e5f6_v2_ai_tables.py                # ← 当前 head：v2 AI 4 张新表 + pgvector/tsv 列
```

`a1b2c3d4e5f6` 一次性补齐：
- `documentchunk.embedding vector(N)` + HNSW 索引；
- `documentchunk.tsv tsvector` + GIN 索引 + 同步触发器；
- 新增 `retrievallog` / `tokenusage` / `promptconfig` 三张表。

## 6. 备份与维护

```bash
# 备份（含 pgvector 数据）
pg_dump -Fc -h localhost -U postgres app > app.dump

# 恢复
pg_restore -d app -h localhost -U postgres app.dump

# 清理过期审计（生产建议跑定时任务）
DELETE FROM retrievallog WHERE created_at < now() - interval '90 days';
DELETE FROM tokenusage   WHERE created_at < now() - interval '180 days';
```

## 7. 性能 tips

- 批量写入 chunk 时禁用 trigger 后重建 tsv 列可显著提速（大文件入库场景）。
- 向量列用 HNSW（默认 m=16, ef_construction=64），ef_search 可在查询时按需调；当数据量 < 100w 时 IVFFlat 召回率更稳。
- 大表加 `pg_partman` 按月分区 `retrievallog` / `tokenusage`。
