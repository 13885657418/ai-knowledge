# pgvector 踩坑笔记

> 应届后端，第一次用 PostgreSQL + pgvector 做向量检索，按设计文档把 MySQL 换成 PG 这一步踩了一堆坑，记录下来。全是后端/数据库问题。

---

## 坑 1：迁移报 `type "vector" does not exist`

**现象**：Day3 写完 `document_chunk` 模型，字段 `embedding = Field(sa_column=Column(Vector(512)))`，跑 `alembic upgrade head` 直接挂：

```
sqlalchemy.exc.ProgrammingError: type "vector" does not exist
```

**排查**：我以为装了 `pgvector` 这个 Python 包就行了，结果那只是客户端绑定。`vector` 是 PostgreSQL 的一个 **扩展类型**，数据库实例里没启用，建表时自然找不到这个类型。

**根因**：扩展没启用。pgvector 需要在目标数据库执行 `CREATE EXTENSION vector;`，而且这必须在任何用到 `vector` 列的建表语句 **之前** 执行。

**解决**：在最早那个 Alembic 迁移的 `upgrade()` 开头加一行：

```python
def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # ...后面才是 create_table(document_chunks ...)
```

加 `IF NOT EXISTS`，重复跑迁移不会报错。容器化时这一步随 `prestart` 的 `alembic upgrade head` 一起跑，不用手动进库执行。

**教训**：扩展是数据库实例级别的能力，跟 Python 包是两回事，换库第一天就该把这行加上。

---

## 坑 2：维度不匹配 `expected 512 dimensions, not 1536`

**现象**：本地先用开源 `bge-small-zh`（512 维）建好了表，后来手痒把 `EMBEDDING_MODEL` 改成 OpenAI 的 `text-embedding-3-small`（1536 维），文档处理任务批量写 chunk 时报：

```
expected 512 dimensions, not 1536
```

**根因**：表里 `embedding` 列是 `vector(512)`，写死的维度。换 embedding 模型 = 换了向量维度，但表结构没跟着变，存的时候维度对不上直接被拒。

**解决**：
1. 把维度收敛成唯一配置项 `EMBEDDING_DIM`，模型加载和建表都引用它，杜绝两处不一致。
2. 真要换维度，必须重建 `embedding` 列并 **重新向量化全部 chunk**（老向量是旧模型空间的，不能跟新向量混用）：

```python
op.execute("ALTER TABLE document_chunks DROP COLUMN embedding")
op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector(1536)")
# 然后把所有 document 重新跑一遍处理任务
```

**教训**：`EMBEDDING_DIM` 一旦定了别乱改，它和 `vector(N)` 是强绑定的。换模型不是改个环境变量那么轻——向量空间变了，存量数据全得重算。

---

## 坑 3：距离算子用错，`<=>` vs `<->` 分不清

**现象**：检索结果排序很怪，明明语义最相关的 chunk 没排在前面。我用的是：

```sql
SELECT id FROM document_chunks ORDER BY embedding <-> :q LIMIT 4;
```

**根因**：pgvector 有三个距离算子，我随手用了 `<->`：
- `<->` L2 欧氏距离
- `<#>` 负内积
- `<=>` 余弦距离

设计文档说的是 **余弦相似度**，embedding 也没归一化，用 L2 排序自然和余弦不一致。而且我拒答阈值是按「余弦相似度」设的，算子用错连阈值判断都是错的。

**解决**：统一用余弦算子，并把「距离」换算成「相似度」再和阈值比：

```sql
SELECT id, 1 - (embedding <=> :q) AS similarity
FROM document_chunks
WHERE knowledge_base_id = :kb_id
ORDER BY embedding <=> :q
LIMIT :m;
```

`<=>` 返回的是余弦距离（越小越近），`1 - 距离` 才是相似度（越大越相关），拒答判定 `max(similarity) < REFUSE_THRESHOLD` 就对了。

**教训**：算子、索引 opclass、阈值语义三者必须配套。余弦就全程余弦，别中途混进 L2。

---

## 坑 4：索引选 ivfflat 还是 hnsw，建完还不走索引

**现象**：数据量上来后检索变慢。建了 ivfflat 索引但 `EXPLAIN` 显示走的还是全表扫描（Seq Scan）。

**排查 + 根因**：
1. **opclass 不配套**：建索引时用了 `vector_l2_ops`，但查询用 `<=>`（余弦）。算子和索引 opclass 不匹配，规划器直接不用这个索引。
2. **ivfflat 建早了**：ivfflat 要先有数据才能聚出有意义的 `lists`，我在空表上建的索引，聚类质量差。
3. **lists 没设**：ivfflat 的 `lists` 参数默认不一定合适，召回率和速度都受影响。

**解决**：
```sql
-- 余弦 + hnsw（建议：召回质量和速度都更好，对写入稍贵）
CREATE INDEX ON document_chunks
USING hnsw (embedding vector_cosine_ops);

-- 或者 ivfflat（要在有数据后建，并设 lists）
CREATE INDEX ON document_chunks
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

选型小结（按设计文档，MVP 用哪个都行，可配置）：
- **hnsw**：召回质量高、查询快、支持增量插入，内存占用大、建索引慢。数据会持续增长选它。
- **ivfflat**：建索引快、内存小，但要先有数据、召回略低，需要调 `lists`。

查询时 hnsw 可调 `SET hnsw.ef_search = 40;`，ivfflat 可调 `SET ivfflat.probes = 10;` 在召回率和速度间权衡。

**教训**：建完索引一定 `EXPLAIN ANALYZE` 确认真的走了索引（看到 `Index Scan` 而不是 `Seq Scan`），别建了个寂寞。opclass 必须和查询算子对应。

---

## 坑 5：tsvector 全文检索列忘了一起维护

**现象**：做混合检索（向量 + BM25）时，BM25 那路一条都召不回。

**根因**：BM25 走的是 `tsv tsvector` 列 + GIN 索引，但我写 chunk 时只填了 `content` 和 `embedding`，`tsv` 列是空的。

**解决**：写 chunk 时同步生成 tsv，中文要注意分词（PG 默认没有中文分词，用 `zhparser` 或先分词再 `to_tsvector('simple', ...)`）：

```sql
UPDATE document_chunks
SET tsv = to_tsvector('simple', content)
WHERE id = :id;
-- 并建 GIN 索引
CREATE INDEX ON document_chunks USING gin (tsv);
```

**教训**：一份 chunk 要同时喂给「向量检索」和「关键词检索」两条路，embedding 和 tsv 得在文档处理任务里一起写好，少写一个就有一路检索是哑的。
