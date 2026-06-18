# 规模化模块

> 对应设计文档第 18 章。当 v1 单机 RAG 无法承载流量、模型成本、效果回归监控时，本项目落地了 3 个独立可运行的规模化模块。模块面向接口编程，业务层（Chat / Agent）切换实现时无感知。

## 18.1 分布式向量分片

### 痛点
- 单机 pgvector 数据集到千万 chunk 量级时，HNSW 重建 / 余弦排序延迟陡增。
- 多租户场景下"热点知识库"会拖慢全局检索。

### 设计

```
                    ┌─────────────┐
   query ──────────▶│ ShardRouter │  分片键 = kb_id
                    └──────┬──────┘
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
       Shard-0(PG)    Shard-1(PG)    Shard-N(PG)
       kb_id%N==0     kb_id%N==1     ...
       [pgvector]     [pgvector]     [pgvector]
            └──────────────┼──────────────┘
                  全局检索：scatter-gather + RRF 归并
```

- 分片键 = `kb_id`：同一知识库的所有 chunk 落同一分片，绝大多数"在某个 KB 内问答"查询是单分片定向路由，零跨分片开销，与单机性能等价。
- scatter-gather：极少数全局检索（如运营后台跨 KB 搜）才并发查所有分片各取 top-k，再做归并重排。
- 演进路径：`VectorStore` 抽象在 v1 已经先行（`PgVectorStore`），分片实现 `ShardedVectorStore` 直接替换注入点，业务层不动。

### 关键文件

- `app/services/vector_store.py` — `VectorStore` 抽象 + `PgVectorStore`（单机） + `ShardedVectorStore`（多分片）。
- `app/services/shard_router.py` — 哈希路由 + scatter-gather 实现。

### 配置

环境变量 `VECTOR_SHARD_DSNS`（逗号分隔多个 DSN）：

```dotenv
# 单机：默认空，等价 PgVectorStore
VECTOR_SHARD_DSNS=

# 多分片
VECTOR_SHARD_DSNS=postgresql+psycopg://u:p@shard0:5432/db,postgresql+psycopg://u:p@shard1:5432/db,postgresql+psycopg://u:p@shard2:5432/db
```

### 跑 demo

```bash
cd backend
python -m app.services.shard_router
```

输出（节选）：
```
[ROUTE]  kb_id=... → shard-2  (definite-route)
[ROUTE]  kb_id=... → shard-0  (definite-route)
[GATHER] global query → fan-out 3 shards, merge by RRF → top-4
```

---

## 18.2 多模型负载均衡 / 调度

### 痛点
- 单 OpenAI key 的 RPM/TPM 上限会变成业务 QPS 上限。
- LLM 偶发 5xx / 超时，需要熔断而不是把错误打给用户。
- 简单 query 用 `gpt-4o-mini` 就够了，复杂 Agent 才需要 `gpt-4o`，成本可优化 5-10 倍。

### 设计

```
ChatService ──▶ LLMRouter ──┬─ ProviderPool（权重 / 健康 / 限速余量）
                            ├─ Strategy（加权轮询 / 最少在途 / 成本路由）
                            ├─ CircuitBreaker（错误率 + 延迟滑窗 → 熔断 + 探活）
                            └─ selected Provider ──▶ 流式 / 非流式调用
                                     └─ 失败 ──▶ fallback 链
```

5 个调度能力：

1. 加权轮询 / 最少在途请求：打散单 key 限速，多 key/多模型并行。
2. 成本 / 能力分级路由：标注 `TaskType.SIMPLE` / `COMPLEX`，简单任务走便宜模型。
3. 熔断：错误率或 P95 延迟越过滑窗阈值 → state=OPEN 摘除流量 → 定时 HALF_OPEN 探活 → 恢复 CLOSED。
4. 限速感知：跟踪各 key 的 RPM/TPM 余量，优先分发余量充足者。
5. 流式兼容 fallback：入口选定 Provider 后保持，只在建连失败前切换 fallback；已开始流式输出的连接不会中途切换，避免拼接乱码。

### 关键文件

- `app/services/llm_router.py`（含 MockProvider，无 key 即可跑）

### 跑 demo

```bash
cd backend
python -m app.services.llm_router
```

输出（节选）：
```
[ROUND-ROBIN]  pick=gpt-4o-mini  weight=3
[BREAKER]      provider=claude-haiku  err_rate=0.42  → state=OPEN
[FALLBACK]     primary=gpt-4o failed → secondary=claude-sonnet
[COST-ROUTE]   task=SIMPLE  → gpt-4o-mini ($0.15/Mtok)
[COST-ROUTE]   task=COMPLEX → gpt-4o      ($2.5/Mtok)
```

### 成本观测

每次调用都把 `model / prompt_tokens / completion_tokens / estimated_cost` 写入 `token_usages` 表。后台可按天聚合：

```sql
SELECT model_name,
       SUM(prompt_tokens)     AS pt,
       SUM(completion_tokens) AS ct,
       SUM(estimated_cost)    AS cost_usd
FROM token_usages
WHERE created_at >= now() - interval '7 days'
GROUP BY model_name
ORDER BY cost_usd DESC;
```

---

## 18.3 RAG 自动化评估 + CI

### 痛点
- "改完 prompt / 换了模型，效果有没有变好？" 肉眼看几个 case 不可靠。
- 上线前需要可量化的回归阈值守门。

### 设计

```
                   golden dataset  (jsonl)
                         │
                         ▼
   ┌─────────────────────────────────────────────┐
   │   EvalService.run(dataset, config)          │
   │ ┌─────────────────────────────────────────┐ │
   │ │ for each item:                          │ │
   │ │   - Retrieval（vec + bm25 + rrf + rerank）│
   │ │   - Generation（可选）                   │ │
   │ │   - 计算指标                              │ │
   │ └─────────────────────────────────────────┘ │
   └─────────────────────────────────────────────┘
                         │
                         ▼
   ┌─ 检索指标 ─────────────────────────────────┐
   │ Hit@k    召回里是否含 expected chunk         │
   │ MRR      首个命中的倒数排名均值              │
   │ Recall@k 命中 / 总相关                       │
   │ Context-Precision  无关 chunk 占比的反向      │
   ├─ 生成指标 ─────────────────────────────────┤
   │ Avg-Relevance  答案与问题的语义相关          │
   │ Faithfulness   答案是否被召回内容支撑（防幻觉）│
   ├─ 运行指标 ─────────────────────────────────┤
   │ Avg-Latency-ms                                │
   │ Estimated-Cost                                │
   │ Refusal-Rate    拒答率                       │
   └────────────────────────────────────────────┘
```

### 关键文件

- `app/services/eval_service.py` — 指标实现 + `ExperimentSnapshot`。
- `scripts/eval_ci.py` — CLI 守门，超出阈值非零退出。
- `app/eval_data/sample_golden.json` — 内置标注集（mock 链路也能跑）。
- API：`POST /eval/run`。

### CI 用法

```yaml
# .github/workflows/eval.yml
- name: RAG Eval Gate
  run: |
    cd backend
    python scripts/eval_ci.py \
      --hit-at-k 0.7 \
      --mrr 0.5 \
      --max-latency-ms 1500
```

任一指标未达 → exit code 非 0 → CI 红。

### 实验对照

通过 `prompt_version` / `top_k` / 模型配置组合多个 `ExperimentSnapshot`，落库到 `eval_data/`，输出 markdown 对照表。例：

| 实验 | prompt_version | top_k | model | Hit@k | MRR | Faithfulness | Avg Cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | v1 | 4 | gpt-4o-mini | 0.71 | 0.52 | 0.79 | $0.0008 |
| +rerank | v1 | 4 | gpt-4o-mini | 0.83 | 0.61 | 0.81 | $0.0009 |
| +prompt v2 | v2 | 4 | gpt-4o-mini | 0.85 | 0.64 | 0.83 | $0.0010 |
| +stronger LLM | v2 | 4 | gpt-4o | 0.85 | 0.64 | 0.91 | $0.0042 |

---

## 模块间协作

三模块在生产部署中同时启用时的链路：

```
                Eval CI（每日 / 每次发布）
                     │
                     ▼
  ┌──────────────────────────────┐
  │  prompt v? + top_k + model   │  ← 候选实验配置
  └────────────┬─────────────────┘
               │
               ▼
        EvalService.run
               │
               ├── Retrieval ── ShardedVectorStore ── (Shard-N pgvector)
               └── Generation ── LLMRouter ── (Provider Pool)
                                        │
                                        └── token_usages（成本 + 失败率）
                                              │
                                              └── 反哺 Router 的成本路由 / 熔断阈值
```

- ShardedVectorStore 让"评估时检索"和"线上检索"复用同一索引；
- LLMRouter 让评估和线上共享熔断 / 成本观测；
- Eval CI 输出的指标趋势反过来影响 Router 的成本路由阈值（哪些任务降级到便宜模型不掉指标）。
