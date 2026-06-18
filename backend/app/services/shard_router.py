"""ShardRouter：分布式向量检索的分片路由层（设计文档 18.1 步骤 3/4）。

架构示意（对应设计文档 18.1）：

                    ┌─────────────┐
   query ──────────▶│ ShardRouter │  按 kb_id 路由
                    └──────┬──────┘
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
       Shard-0(PG)    Shard-1(PG)    Shard-N(PG)
       kb_id%N==0     kb_id%N==1     ...
       [pgvector]     [pgvector]     [pgvector]
            └──────────────┼──────────────┘
                  全局检索时 scatter-gather + 归并重排

设计要点：
- 分片键 = kb_id（同一知识库的 chunk 落同一分片 → 向量检索天然按知识库隔离，
  绝大多数查询单分片即可完成，无需跨分片聚合）；
- route(kb_id)：kb_id 哈希 % N 选目标分片 engine，定向读写；
- scatter_gather()：仅极少数全局检索需要——并发查所有分片各取 top-k，
  应用层做全局归并重排（按相似度，可选 RRF），再取最终 top-k。

可运行性：默认只配置 1 个分片（指向主库 engine），单机即可运行；
通过环境变量 VECTOR_SHARD_DSNS（逗号分隔）可声明多分片 DSN，
此时按需 lazy 创建各分片 async engine。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

try:
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
except Exception:  # noqa: BLE001 - 保证无 sqlalchemy 时仍可 py_compile
    AsyncEngine = Any  # type: ignore
    create_async_engine = None  # type: ignore


def _hash_kb_id(kb_id: Any) -> int:
    """把 kb_id（UUID 或字符串）稳定映射为非负整数，用于取模分片。

    使用 md5 而非内置 hash()，避免进程间 hash 随机化导致路由漂移。
    """
    if isinstance(kb_id, uuid.UUID):
        return kb_id.int
    digest = hashlib.md5(str(kb_id).encode("utf-8")).hexdigest()
    return int(digest, 16)


class ShardRouter:
    def __init__(
        self,
        shard_dsns: list[str] | None = None,
        default_engine: Any | None = None,
    ) -> None:
        """:param shard_dsns: 各分片的 async DSN 列表；为空则读环境变量，
        再为空则退化为单分片（使用 default_engine 或主库 engine）。
        :param default_engine: 单分片场景复用的现成 engine（通常是 app.core.db.engine）。
        """
        self._engines: dict[int, Any] = {}
        self._dsns: list[str] = shard_dsns or self._load_dsns_from_env()
        self._default_engine = default_engine

        if not self._dsns:
            # 单分片模式：复用主库 engine，保证开发机单库即可运行
            engine = default_engine or self._load_main_engine()
            self._engines[0] = engine
            self.shard_count = 1
        else:
            self.shard_count = len(self._dsns)

    @staticmethod
    def _load_dsns_from_env() -> list[str]:
        raw = os.getenv("VECTOR_SHARD_DSNS", "").strip()
        if not raw:
            return []
        return [d.strip() for d in raw.split(",") if d.strip()]

    @staticmethod
    def _load_main_engine() -> Any:
        """惰性获取主库 engine，避免在无 DB 配置时 import 即报错。"""
        try:
            from app.core.db import engine

            return engine
        except Exception:  # noqa: BLE001
            return None

    def _engine_for_shard(self, index: int) -> Any:
        """获取/创建指定分片 index 的 engine（lazy 初始化）。"""
        if index in self._engines:
            return self._engines[index]
        if self._dsns and create_async_engine is not None:
            engine = create_async_engine(self._dsns[index])
            self._engines[index] = engine
            return engine
        # 兜底返回单分片 engine
        return self._engines.get(0)

    def shard_index(self, kb_id: Any) -> int:
        """kb_id → 分片下标（kb_id % N）。"""
        return _hash_kb_id(kb_id) % self.shard_count

    def route(self, kb_id: Any) -> Any:
        """按 kb_id 路由到目标分片 engine（设计文档 18.1 步骤 3 路由层）。"""
        return self._engine_for_shard(self.shard_index(kb_id))

    async def scatter_gather(
        self,
        query_embedding: list[float],
        k: int,
        use_rrf: bool = False,
        rrf_k: int = 60,
    ) -> list[dict[str, Any]]:
        """全局检索：并发查所有分片各取 top-k，再全局归并重排（设计文档 18.1 步骤 4）。

        :param query_embedding: 查询向量。
        :param k: 每分片及最终返回的 top-k。
        :param use_rrf: True 用 RRF 融合多分片排名；False 直接按相似度合并。
        :return: 全局 top-k 命中列表。
        """
        import asyncio

        async def _query_shard(index: int) -> list[dict[str, Any]]:
            engine = self._engine_for_shard(index)
            return await self._search_one_shard(engine, query_embedding, k)

        per_shard = await asyncio.gather(
            *[_query_shard(i) for i in range(self.shard_count)],
            return_exceptions=True,
        )
        # 过滤掉异常分片（单分片失败不应让全局检索崩溃）
        valid: list[list[dict[str, Any]]] = [
            r for r in per_shard if isinstance(r, list)
        ]

        if use_rrf:
            return self._merge_rrf(valid, k, rrf_k)
        return self._merge_by_score(valid, k)

    @staticmethod
    async def _search_one_shard(
        engine: Any, query_embedding: list[float], k: int
    ) -> list[dict[str, Any]]:
        """在单个分片上做 pgvector 余弦 top-k 检索。

        engine 为空（无 DB 环境）时返回空列表，保证可 py_compile / 可跑测试。
        """
        if engine is None:
            return []
        from sqlalchemy import text

        # 1 - cosine_distance 作为相似度分数（pgvector <=> 为余弦距离）
        sql = text(
            """
            SELECT id, document_id, content,
                   1 - (embedding <=> :qvec) AS score
            FROM documentchunk
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> :qvec
            LIMIT :k
            """
        )
        vec_literal = "[" + ",".join(str(x) for x in query_embedding) + "]"
        async with engine.connect() as conn:
            result = await conn.execute(sql, {"qvec": vec_literal, "k": k})
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

    @staticmethod
    def _merge_by_score(
        per_shard: list[list[dict[str, Any]]], k: int
    ) -> list[dict[str, Any]]:
        """按相似度分数全局归并取 top-k（按 chunk_id 去重保留最高分）。"""
        best: dict[str, dict[str, Any]] = {}
        for shard in per_shard:
            for h in shard:
                cid = h.get("chunk_id", "")
                if cid not in best or (h.get("score") or 0.0) > (best[cid].get("score") or 0.0):
                    best[cid] = h
        merged = list(best.values())
        merged.sort(key=lambda h: h.get("score") or 0.0, reverse=True)
        return merged[:k]

    @staticmethod
    def _merge_rrf(
        per_shard: list[list[dict[str, Any]]], k: int, rrf_k: int
    ) -> list[dict[str, Any]]:
        """RRF（Reciprocal Rank Fusion）融合多分片排名（设计文档 18.1 可用 RRF）。

        score = Σ 1/(rrf_k + rank)，跨分片对同一 chunk_id 累加。
        """
        scores: dict[str, float] = {}
        payload: dict[str, dict[str, Any]] = {}
        for shard in per_shard:
            for rank, hit in enumerate(shard):
                cid = hit.get("chunk_id", "")
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
                payload.setdefault(cid, hit)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        out: list[dict[str, Any]] = []
        for cid, fused in ranked[:k]:
            item = dict(payload[cid])
            item["rrf_score"] = fused
            out.append(item)
        return out
