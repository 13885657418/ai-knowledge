# RAG 检索链路

> 详细说明文档摄取、切分、混合检索、融合、重排、拒答的实现逻辑。对应代码：`app/services/chunking.py`、`embedding_service.py`、`retrieval_service.py`、`reranker.py`、`chat_service.py`。

## 1. 文档摄取

```
upload(file)
   │
   ▼
保存到 /app/uploads/<uuid>.<ext>，写入 Document(status=pending)
   │
   ▼ enqueue
worker: process_document(doc_id)
   │
   ├─ 1. 提取文本（按 file_type）
   │     - .txt / .md  →  open().read()
   │     - .docx       →  python-docx
   │     - .pdf        →  pypdf（缺失时降级失败，写 error_message）
   │
   ├─ 2. Chunking.split(content, strategy)
   │     - fixed       固定 char 窗口 + overlap（默认 800 / 100）
   │     - markdown    按 # / ## / ### 标题切分，标题作为 chunk 头部
   │     - paragraph   按 \n\n 段落，超长再 fixed 二次切
   │
   ├─ 3. EmbeddingService.batch_embed(chunks)
   │     - mock     哈希派生确定性向量
   │     - openai   text-embedding-3-small（批量，自动重试）
   │
   └─ 4. INSERT document_chunks
         - embedding 列：vector(EMBEDDING_DIM)
         - tsv 列：由 PG 触发器从 content 同步生成
         - knowledge_base_id / owner_id 冗余写入（便于检索过滤 + 分片）
         - 提交后将 Document.status 置 ready，写入 chunk_count
```

切分策略对比：

| 策略 | 适用 | 优势 | 缺点 |
| --- | --- | --- | --- |
| `fixed` | 通用 | 简单，可控边界 | 可能切断语义 |
| `markdown` | 技术文档 / Wiki | 保留标题层级，召回上下文完整 | 需要良好的 # 结构 |
| `paragraph` | 自然语言 | 段落边界即语义边界 | 段落太长仍需二次切 |

## 2. 混合检索（Hybrid Search）

`RetrievalService.hybrid_search(query, top_k)` 实现三步：

### 2.1 双路召回

**向量召回**（pgvector 余弦距离 `<=>`）：

```sql
SELECT id, document_id, chunk_index, content,
       1 - (embedding <=> :q_vec::vector) AS score
FROM document_chunks
WHERE knowledge_base_id = :kb_id
  AND embedding IS NOT NULL
ORDER BY embedding <=> :q_vec::vector
LIMIT :candidate_k;
```

**BM25 / 全文召回**（PG 原生）：

```sql
SELECT id, document_id, chunk_index, content,
       ts_rank_cd(tsv, plainto_tsquery('simple', :q_text)) AS score
FROM document_chunks
WHERE knowledge_base_id = :kb_id
  AND tsv @@ plainto_tsquery('simple', :q_text)
ORDER BY score DESC
LIMIT :candidate_k;
```

> 中文场景：`tsv` 由 `simple` 配置（保留原始 token），可叠加 jieba 等分词预处理；如果完全没有 `tsv` 数据，降级到 `ILIKE '%<word>%'`。

`candidate_k` 默认 20，控制召回宽度；最终只取 `top_k` 个（默认 4）。

### 2.2 RRF 融合

> Reciprocal Rank Fusion，无监督融合两路打分量纲，**只用名次不用绝对分数**，对异质打分非常稳健。

```
score(d) = Σ over ranking r ∈ {vector, bm25}  1 / (RRF_K + rank_r(d))
```

`RRF_K` 默认 60。两路都召回的 chunk 会被显著拉高。

### 2.3 Rerank（可选）

输入：query + 融合后的 candidate（默认 20 个）；
输出：每个 candidate 一个相关性分数。

模型：`BAAI/bge-reranker-base`（Cross-Encoder，HuggingFace）。

依赖缺失（无 `sentence-transformers` / 无 GPU）时**自动降级**为"分数透传"——直接用 RRF 分数排序，不重排但链路不断。

最终按 rerank score 降序取 `top_k`。

## 3. 拒答机制

```python
if rerank_scores and max(rerank_scores) < REFUSAL_THRESHOLD:
    return refusal_response()
```

- 默认 `REFUSAL_THRESHOLD=0.3`（mock 模式建议 0.0 关闭）。
- 拒答时 `is_refused=True`，`citations=[]`，前端可以提示"知识库未覆盖该问题"。
- 流式场景下额外 emit 一个 `refusal` 事件再 `done`。
## 4. 引用 / 可解释性

每条返回的 chunk 携带：

```json
{
  "chunk_id": "<uuid>",
  "document": "spec.md",
  "chunk_index": 3,
  "preview": "切片前 200 字..."
}
```

调用层 `ChatService.ask()` 在拼 prompt 时把 `[1]/[2]/[3]/[4]` 编号注入上下文，要求模型在答案中标注引用编号。

## 5. 检索可解释性日志

每次检索（无论是否命中拒答）都写一行 `retrieval_logs`：

```
session_id, query_text, rewritten_query,
retrieved_chunk_ids: jsonb,
scores: jsonb,                 -- {"vector":[...], "bm25":[...], "rrf":[...], "rerank":[...]}
top_k, is_refused, latency_ms
```

后续做：

- 效果归因（同一 query 在不同 prompt_version 下的对比）；
- 异常诊断（哪些 query 命中拒答 / 高延迟）；
- 评估数据反哺（人工标注 retrieved_chunk_ids 是否相关 → 扩充 golden set）。

## 6. 性能要点

- `embedding`、`tsv` 列均建索引（HNSW / GIN）。
- `knowledge_base_id` 列单独索引，确保分片场景下"按 KB 过滤"不退化全表扫。
- BM25 走原生 `tsvector`，比应用层 BM25Okapi 快一个数量级，且天然走 GIN 索引。
- candidate_k 与 top_k 解耦：召回宽 / 重排窄，是质量 vs 延迟的关键旋钮。
- Embedding 用批量接口（`batch_embed`），文档摄取时按 64-128 一批降低 RTT。
