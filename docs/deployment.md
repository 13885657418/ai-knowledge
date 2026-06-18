# 部署指南

> 涵盖本地 / 测试 / 生产三种部署形态。生产建议放置于内网 + 反向代理（Nginx / Traefik）后。

## 1. 本地开发

详见 [`quickstart.md`](quickstart.md)。一键：

```bash
cp .env.example .env
docker compose -f compose.local.yml up --build
```

## 2. 生产参考拓扑

```
            ┌─────────────────┐
            │   Nginx / ALB   │  (TLS 终结 / 限流 / WAF)
            └────────┬────────┘
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
   FastAPI N1    FastAPI N2    FastAPI N3   (uvicorn / gunicorn-uvloop)
       │             │             │
       └──────┬──────┴──────┬──────┘
              ▼             ▼
       Postgres(主)    Redis Cluster
       └ 物理 / 逻辑 副本
              │
       (分片场景：多个 PG 实例，各自起 pgvector，VECTOR_SHARD_DSNS 列出)
```

## 3. Dockerfile / 镜像

`backend/Dockerfile` 单阶段：

- 基于 `python:3.11-slim`；
- `pip install -r requirements.txt`；
- 入口 `bash scripts/prestart.sh && fastapi run app/main.py --host 0.0.0.0 --port 8000`；
- 使用 `app-uploads` volume 持久化上传文件。

构建：

```bash
docker build -t kb-rag-backend:1.0.0 -f backend/Dockerfile .
```

## 4. 环境变量分级

| 等级 | 必改 | 备注 |
| --- | --- | --- |
| `SECRET_KEY` | ✅ | `python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `POSTGRES_PASSWORD` | ✅ | 生产环境强制 |
| `FIRST_SUPERUSER_PASSWORD` | ✅ | 首次启动后立即改 |
| `BACKEND_CORS_ORIGINS` | ✅ | 收紧到具体前端域名 |
| `ENVIRONMENT` | ✅ | `production` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | 按需 | 不用 mock 时 |
| `RATE_LIMIT_PER_MINUTE` | 按需 | 默认 30 / 用户 / 分钟 |
| `LANGFUSE_*` | 按需 | 接 Langfuse |
| `SENTRY_DSN` | 按需 | 错误上报 |
| `VECTOR_SHARD_DSNS` | 按需 | 多分片才填 |

`config.py` 启动校验：默认 secret 在非 local 环境直接抛错，避免误用。

## 5. 反向代理 / SSE

Nginx 关键片段（SSE 必须关闭 buffering）：

```nginx
location /api/v1/chat/sessions/ {
    proxy_pass http://backend;

    # SSE
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding on;
    proxy_read_timeout 600s;
}

location / {
    proxy_pass http://backend;
}
```

> 后端响应里已经带了 `X-Accel-Buffering: no`，Nginx 也最好显式关 buffering 防被覆盖。

## 6. 数据库

### 初始化 / 迁移

```bash
# 容器内
alembic upgrade head
```

`backend/scripts/prestart.sh` 在每次容器启动时自动跑迁移 + 初始化超管。

### pgvector 扩展

确保镜像或 PG 实例装好 pgvector ≥ 0.5：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 备份

```bash
pg_dump -Fc -d app -h <host> -U <user> -f app-$(date +%F).dump
```

恢复时建议先 `CREATE EXTENSION vector;` 再 `pg_restore`。

## 7. 监控 / 日志

| 维度 | 工具 | 接入方式 |
| --- | --- | --- |
| Trace | Langfuse | `LANGFUSE_*` env，自动 wrap LLM 调用 |
| 错误 | Sentry | `SENTRY_DSN` env |
| 指标 | Prometheus | （建议自行加 `prometheus-fastapi-instrumentator`） |
| 日志 | stdout JSON | `loguru` / 标准 logging，统一收 ELK / Loki |
| 业务审计 | 内部表 | `retrievallog` / `tokenusage` 直接查 SQL |

## 8. 容量规划

单实例参考：

| 维度 | 估算 |
| --- | --- |
| QPS | 50-80（取决于 LLM 延迟） |
| 内存 | 2 GB（不含 reranker） / 4 GB（含 bge-reranker-base） |
| Postgres | 4C8G，10w chunk 量级 |
| 单 chunk 索引体积 | ~6 KB（vector(1536) + tsv + 元数据） |

10w chunk × 6 KB ≈ 600 MB，HNSW 索引另算 ~30%。百万级建议直接走分片（见 [`scaling.md`](scaling.md)）。

## 9. 升级流程

1. 跑 eval CI：`python backend/scripts/eval_ci.py`，确认指标不退化；
2. 数据库备份；
3. 拉新镜像；
4. 蓝绿 / 滚动重启 FastAPI；
5. 启动后跑一次 `/utils/health-check/`；
6. 流量切回。

> Prompt 升级走 `POST /prompts/{id}/activate` 热切换，不需要重启。

## 10. 安全清单

- [ ] 修改 `SECRET_KEY` / `POSTGRES_PASSWORD` / `FIRST_SUPERUSER_PASSWORD`
- [ ] `ENVIRONMENT=production`
- [ ] `BACKEND_CORS_ORIGINS` 收紧
- [ ] TLS 在 Nginx / LB 层启用
- [ ] Postgres 不暴露公网，限定到 VPC
- [ ] Redis 启用密码 / 限定 ACL
- [ ] OpenAPI `/docs` 在生产是否需要关闭（`docs_url=None`）
- [ ] 上传目录 `/app/uploads` 容量与清理策略
- [ ] 限流阈值 `RATE_LIMIT_PER_MINUTE` 按 SLA 调整
