"""VectorStore：向量存储抽象接口（设计文档 18.1 "设计落点"）。

抽象 add / search / delete 三个操作。单机 pgvector 与分片实现共用同一接口，
业务层（RetrievalService 等）只面向接口编程，从单机演进到分片时只换实现、
业务层无感知——这正是设计文档 18.1 最后一段强调的"演进时只换实现"。

  ┌────────────────────────────────────────────┐
  │  业务层（RetrievalService / AgentService）   │
  └───────────────────┬────────────────────────┘
                      │ 依赖抽象 VectorStore
        ┌─────────────┴──────────────┐
        ▼                            ▼
  PgVectorStore               ShardedVectorStore
  （单机 pgvector）            （ShardRouter 路由 + scatter-gather）
"""

from __future__ import annotations

import abc
import uuid
from typing import Any


class VectorStore(abc.ABC):
    """向量存储统一接口。"""

    @abc.abstractmethod
    async def add(
        self,
        chunk_id: Any,
        embedding: list[float],
        kb_id: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入/更新单个向量。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        k: int,
        kb_id: Any | None = None,
    ) -> list[dict[str, Any]]:
        """向量相似度检索 top-k。kb_id 给定时按知识库过滤（分片场景定向路由）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, chunk_id: Any) -> None:
        """删除单个向量。"""
        raise NotImplementedError


class PgVectorStore(VectorStore):
    """单机 pgvector 实现：基于 document_chunks 表的原生余弦检索。

    用 raw SQL（设计文档要求 from sqlalchemy import text）执行 pgvector 的
    `<=>` 余弦距离排序。session 为空时各方法安全降级（返回空 / 跳过），
    保证无 DB 环境也能 py_compile 并被单测覆盖。
    """

    def __init__(self, session: Any | None = None) -> None:
        self.session = session

    @staticmethod
    def _vec_literal(embedding: list[float]) -> str:
        return "[" + ",".join(str(x) for x in embedding) + "]"

    async def add(
        self,
        chunk_id: Any,
        embedding: list[float],
        kb_id: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.session is None:
            return
        from sqlalchemy import text

        sql = text(
            "UPDATE documentchunk SET embedding = :vec WHERE id = :cid"
        )
        await self.session.execute(
            sql, {"vec": self._vec_literal(embedding), "cid": str(chunk_id)}
        )
        await self.session.commit()

    async def search(
        self,
        query_embedding: list[float],
        k: int,
        kb_id: Any | None = None,
    ) -> list[dict[str, Any]]:
        if self.session is None:
            return []
        from sqlalchemy import text

        # 按 kb_id 过滤即设计文档 18.1 "按租户隔离的局部检索"
        where = "embedding IS NOT NULL"
        params: dict[str, Any] = {
            "qvec": self._vec_literal(query_embedding),
            "k": k,
        }
        if kb_id is not None:
            where += " AND knowledge_base_id = :kb_id"
            params["kb_id"] = str(kb_id)

        sql = text(
            f"""
            SELECT id, document_id, content,
                   1 - (embedding <=> :qvec) AS score
            FROM documentchunk
            WHERE {where}
            ORDER BY embedding <=> :qvec
            LIMIT :k
            """
        )
        result = await self.session.execute(sql, params)
        rows = result.mappings().all()
        return [
            {
                "chunk_id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "content": row["content"],
                "score": float(row["score"]),
            }
            for row in rows
        ]

    async def delete(self, chunk_id: Any) -> None:
        if self.session is None:
            return
        from sqlalchemy import text

        await self.session.execute(
            text("DELETE FROM documentchunk WHERE id = :cid"),
            {"cid": str(chunk_id)},
        )
        await self.session.commit()


class ShardedVectorStore(VectorStore):
    """分片实现：写入/单库检索经 ShardRouter 定向路由；
    跨知识库全局检索走 scatter-gather + 全局归并重排（设计文档 18.1 步骤 4）。
    """

    def __init__(self, router: Any) -> None:
        # router: ShardRouter（解耦导入，避免循环依赖）
        self.router = router

    async def add(
        self,
        chunk_id: Any,
        embedding: list[float],
        kb_id: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        engine = self.router.route(kb_id)
        if engine is None:
            return
        from sqlalchemy import text

        vec = "[" + ",".join(str(x) for x in embedding) + "]"
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE documentchunk SET embedding = :vec WHERE id = :cid"),
                {"vec": vec, "cid": str(chunk_id)},
            )

    async def search(
        self,
        query_embedding: list[float],
        k: int,
        kb_id: Any | None = None,
    ) -> list[dict[str, Any]]:
        if kb_id is not None:
            # 定向单分片检索：向量检索天然按知识库隔离，无需跨分片
            engine = self.router.route(kb_id)
            return await self.router._search_one_shard(engine, query_embedding, k)
        # 全局检索（极少数）：scatter-gather + 归并重排
        return await self.router.scatter_gather(query_embedding, k)

    async def delete(self, chunk_id: Any) -> None:
        # 无 kb_id 时无法定位分片；真实实现可维护 chunk_id→kb_id 映射或广播删除。
        # 这里广播到全部分片以保证幂等删除。
        import asyncio

        from sqlalchemy import text

        async def _del(index: int) -> None:
            engine = self.router._engine_for_shard(index)
            if engine is None:
                return
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM documentchunk WHERE id = :cid"),
                    {"cid": str(chunk_id)},
                )

        await asyncio.gather(
            *[_del(i) for i in range(self.router.shard_count)],
            return_exceptions=True,
        )


# 类型别名导出，便于业务层类型标注
__all__ = ["VectorStore", "PgVectorStore", "ShardedVectorStore", "uuid"]
