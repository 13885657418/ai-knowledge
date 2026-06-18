# 快速开始

> 目标：5 分钟在本地把后端跑起来，并完成 "上传文档 → 提问 → 拿到带引用的回答" 的端到端 smoke-test。`mock` provider 模式下全程无需任何 LLM / Embedding API key。

## 0. 前置依赖

| 依赖 | 版本 | 备注 |
| --- | --- | --- |
| Docker / Docker Compose | 任意近期版本 | 推荐方式 |
| 或 Python | 3.10+ | 不用 Docker 时 |
| 或 Postgres + pgvector | PG 17 + pgvector ≥ 0.5 | 不用 Docker 时 |
| 或 Redis | 7.x | 不用 Docker 时 |

## 1. 用 Docker Compose 一键启动（推荐）

```bash
git clone <你的 fork 地址>
cd ai-knowledge

cp .env.example .env

docker compose -f compose.local.yml up --build
```

启动后：

| 服务 | 地址 |
| --- | --- |
| FastAPI | http://localhost:8000 |
| OpenAPI 交互文档 | http://localhost:8000/docs |
| Postgres | localhost:5432 (user/pwd 见 `.env`) |
| Redis | localhost:6379 |

> 第一次启动会自动跑 `alembic upgrade head` 完成迁移，并创建初始超管 `admin@example.com` / `changethis`（在 `.env` 中可改）。

### 验证已就绪

```bash
curl http://localhost:8000/api/v1/utils/health-check/
# {"status":"ok"}
```

## 2. 不用 Docker：本地 Python 启动

```bash
# 准备 Postgres(pgvector) + Redis 后

cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 编辑 ../.env，POSTGRES_SERVER 改为本机 Postgres 地址
alembic upgrade head

fastapi run app/main.py --host 0.0.0.0 --port 8000 --reload
```

## 3. 端到端 smoke-test

下面用 `curl` 走一遍 "登录 → 建知识库 → 上传文档 → 等待处理 → 提问"。

```bash
BASE=http://localhost:8000/api/v1

# 3.1 登录拿 token
TOKEN=$(curl -s -X POST $BASE/login/access-token \
  -d "username=admin@example.com&password=changethis" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 3.2 创建知识库
KB_ID=$(curl -s -X POST $BASE/knowledge-bases/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"demo-kb","description":"smoke test"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "kb_id=$KB_ID"

# 3.3 上传一个文档（异步处理）
curl -X POST $BASE/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "knowledge_base_id=$KB_ID" \
  -F "file=@README.md"

# 3.4 等几秒，等待 processing_status 变为 ready
sleep 5
curl $BASE/documents/?knowledge_base_id=$KB_ID \
  -H "Authorization: Bearer $TOKEN"

# 3.5 创建会话
SESSION_ID=$(curl -s -X POST $BASE/chat/sessions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"knowledge_base_id\":\"$KB_ID\",\"title\":\"demo\"}" \
  | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 3.6 提问（非流式）
curl -X POST $BASE/chat/sessions/$SESSION_ID/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"这个项目有什么核心能力？","top_k":4}'

# 3.7 提问（SSE 流式）—— 终端会逐 token 打印
curl -N -X POST $BASE/chat/sessions/$SESSION_ID/ask/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"这个项目有什么核心能力？","top_k":4}'
```

`mock` 模式下，Embedding 用确定性伪向量，LLM 返回一个把召回片段拼起来的固定回答。整条 RAG 链路（向量召回 / BM25 / RRF / Rerank / 拒答 / SSE / 落库）都是真的，只有"模型推理"是 mock。

## 4. 切换到真实模型

编辑 `.env`：

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
OPENAI_API_KEY=sk-xxx

LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
```

> **维度变更注意**：`EMBEDDING_DIM` 改了之后，`document_chunks.embedding` 的 `vector(N)` 列宽与已有数据都需要重建。本地 demo 最简单做法是 `docker compose down -v` 清空数据后再上来。

切到 Anthropic：

```dotenv
LLM_PROVIDER=claude
LLM_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=sk-ant-xxx
```

## 5. 跑规模化模块的 demo（无需 DB）

三个文件都自带 `__main__` 演示，不依赖数据库或 API key：```bash
cd backend
python -m app.services.llm_router      # 多模型负载均衡 + 熔断 + fallback
python -m app.services.shard_router    # 向量分片路由 + scatter-gather
python scripts/eval_ci.py              # RAG 自动化评估指标
```

## 6. 运行测试

```bash
cd backend
bash scripts/test.sh                   # 跑 pytest（含 API 集成）
```

或单独：

```bash
pytest tests/api -v
pytest tests/crud -v
```

## 7. 数据库迁移

```bash
cd backend
alembic upgrade head                   # 升级到最新
alembic revision --autogenerate -m "add new column"   # 生成新迁移
alembic downgrade -1                   # 回滚一步
```

当前 head：`a1b2c3d4e5f6_v2_ai_tables`（新增 RAG/Agent/Eval 相关 4 张表 + pgvector / tsvector 列）。

## 8. 常见问题

**Q: 启动报 `extension "vector" does not exist`？**
A: Postgres 镜像必须是 `pgvector/pgvector:pg17`（`compose.local.yml` 已指定）。本地裸装 PG 需手动 `CREATE EXTENSION vector;`，迁移会自动尝试创建。

**Q: 流式接口拿到一坨 JSON 不是 SSE？**
A: `curl` 必须加 `-N` 关闭 buffer。前端用 `fetch + ReadableStream` 而非 `EventSource`（POST 不支持）。

**Q: 上传文档后 processing_status 一直是 processing？**
A: 检查 `backend` 日志；常见是缺少 `pypdf`（按 `requirements.txt` 安装即可）或文件超大。

**Q: 评分一直是 0？**
A: `mock` embedding 是确定性伪向量，相似度本来就低；切真实 embedding 即可。或把 `REFUSAL_THRESHOLD` 调到 0.0 关闭拒答。
