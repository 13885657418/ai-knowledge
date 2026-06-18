"""KbSearchTool：知识库检索工具（设计文档 4.8 / 8.3 的核心 Agent 工具）。

这是 ReAct 循环里最关键的工具——它把 RetrievalService.hybrid_search 暴露给模型，
让 Agent 能"自主决定检索什么、检索几次"，而非写死一次检索。

容错策略：RetrievalService 由另一位同学实现，可能尚不存在或签名不同。
因此这里 try/except 导入，并在不可用时优雅降级为对 document_chunks.content
的简单 ILIKE 文本检索（仍能跑出可用结果，保证 Agent 链路不断）。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.services.tools.base import BaseTool


class KbSearchTool(BaseTool):
    name = "search_knowledge_base"
    description = (
        "在指定知识库中检索与查询最相关的文档片段，返回 top-k 片段及其来源。"
        "这是回答任何基于知识库内容的问题时应优先使用的工具——"
        "先检索证据，再基于证据作答，避免凭空臆测。"
    )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索查询语句（自然语言）",
                },
                "kb_id": {
                    "type": "string",
                    "description": "目标知识库 ID（UUID 字符串）",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回片段数量，默认 4",
                    "default": 4,
                },
            },
            "required": ["query", "kb_id"],
        }

    async def run(self, args: dict[str, Any], session: Any | None = None) -> Any:
        query = args.get("query")
        kb_id = args.get("kb_id")
        top_k = int(args.get("top_k", 4) or 4)
        if not query or not kb_id:
            return {"error": "缺少必填参数 query / kb_id"}

        if session is None:
            # mock 路径：无 DB 时返回占位片段，保证 Agent demo 可运行
            # （放在 UUID 校验之前，demo 用非 UUID 的 kb_id 也能跑出引用）
            return {
                "query": query,
                "hits": [
                    {
                        "chunk_id": "mock-chunk-1",
                        "content": f"(mock) 关于「{query}」的知识库片段占位内容。",
                        "score": 0.42,
                        "document_id": "mock-doc",
                    }
                ],
                "note": "session 不可用，返回 mock 检索结果",
            }

        try:
            kb_uuid = uuid.UUID(str(kb_id))
        except (ValueError, AttributeError):
            return {"error": f"非法的 kb_id: {kb_id!r}"}

        # 优先走真实的混合检索服务（向量 + BM25 + rerank）
        try:
            from app.services.embedding_service import EmbeddingService
            from app.services.retrieval_service import RetrievalService

            embedding_service = EmbeddingService()
            query_embedding = await embedding_service.embed_query(query)
            service = RetrievalService(session)
            results = await service.hybrid_search(
                kb_uuid, query, query_embedding, top_k
            )
            return {"query": query, "hits": _normalize_hits(results)}
        except Exception as exc:  # noqa: BLE001 - 降级到 ILIKE 文本检索
            return await self._fallback_ilike_search(query, kb_uuid, top_k, session, exc)

    @staticmethod
    async def _fallback_ilike_search(
        query: str,
        kb_uuid: uuid.UUID,
        top_k: int,
        session: Any,
        reason: Exception | None = None,
    ) -> dict[str, Any]:
        """RetrievalService 不可用时的兜底：对 chunk.content 做 ILIKE 模糊匹配。"""
        from sqlalchemy import select

        from app.models import DocumentChunk

        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.knowledge_base_id == kb_uuid)
            .where(DocumentChunk.content.ilike(f"%{query}%"))  # type: ignore[attr-defined]
            .limit(top_k)
        )
        result = await session.execute(stmt)
        chunks = result.scalars().all()
        hits = [
            {
                "chunk_id": str(c.id),
                "content": c.content,
                "score": None,  # ILIKE 无相似度分数
                "document_id": str(c.document_id),
                "chunk_index": c.chunk_index,
            }
            for c in chunks
        ]
        return {
            "query": query,
            "hits": hits,
            "fallback": "ilike",
            "reason": str(reason) if reason else None,
        }


def _normalize_hits(results: Any) -> list[dict[str, Any]]:
    """把 RetrievalService 的返回（结构未知）尽量归一化为统一 hit 结构。"""
    normalized: list[dict[str, Any]] = []
    for item in results or []:
        if isinstance(item, dict):
            normalized.append(item)
            continue
        # 兜底：尝试从对象属性读取常见字段
        normalized.append(
            {
                "chunk_id": str(getattr(item, "id", getattr(item, "chunk_id", ""))),
                "content": getattr(item, "content", ""),
                "score": getattr(item, "score", None),
                "document_id": str(getattr(item, "document_id", "")),
            }
        )
    return normalized
