# 文档总览

本目录是企业知识库 AI 助手后端的工程文档索引。

## 阅读顺序建议

| 角色 | 推荐顺序 |
| --- | --- |
| 第一次接触项目 | [`quickstart.md`](quickstart.md) → [`architecture.md`](architecture.md) |
| 想看完整设计原稿 | [`design-v2.md`](design-v2.md)（含 §1-17 业务/接口/存储/Agent/安全等 + §18 规模化） |
| 二次开发 / 改业务 | [`architecture.md`](architecture.md) → [`api.md`](api.md) → [`database.md`](database.md) |
| 调 RAG 效果 | [`rag-pipeline.md`](rag-pipeline.md) → [`scaling.md`](scaling.md#183-rag-自动化评估--ci) |
| 加 / 改 Agent 工具 | [`agent-design.md`](agent-design.md) → [`api.md`](api.md#工具--agent) |
| 部署 / 运维 | [`deployment.md`](deployment.md) → [`scaling.md`](scaling.md) |

## 文档清单

- [`design-v2.md`](design-v2.md) — **权威设计文档 v2**（含第 18 章规模化方向）。代码内所有 `# 设计文档 X.Y` 注释均对应此文档章节。
- [`quickstart.md`](quickstart.md) — 5 分钟本地搭起来 + 端到端 smoke-test。
- [`architecture.md`](architecture.md) — 系统总览、模块边界、关键调用时序、降级策略。
- [`api.md`](api.md) — REST API 字段级参考（鉴权 / KB / 文档 / 会话 / Prompt / 工具 / Eval）。
- [`rag-pipeline.md`](rag-pipeline.md) — 文档摄取、切分、向量召回、BM25、RRF 融合、Rerank、拒答。
- [`agent-design.md`](agent-design.md) — ReAct 工具循环、Function Calling、内置工具、MCP 桥接、防失控。
- [`database.md`](database.md) — 表结构、索引、迁移、pgvector / tsvector 列、性能 tips。
- [`scaling.md`](scaling.md) — 第 18 章规模化在仓库中的代码落地（与 `design-v2.md` §18 对照阅读）。
- [`deployment.md`](deployment.md) — Docker 部署、反向代理、SSE 配置、监控、安全清单。

## 配套资料

- [`../README.md`](../README.md) — 项目门面。
- [`../notes/`](../notes/) — 开发踩坑实录（pgvector / SSE / Agent 循环 / 多模型降级）。
- OpenAPI 交互文档：本地 http://localhost:8000/docs
