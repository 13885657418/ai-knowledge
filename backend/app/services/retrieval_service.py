"""混合检索服务（设计文档 8.2 步骤 3-4）。

实现三路能力并以 RRF 融合 + rerank 收敛：
- vector_search: pgvector 余弦距离（<=>）排序，按知识库过滤、排除空向量。
- bm25_search:   PG 全文检索（tsv @@ plainto_tsquery + ts_rank），降级 ILIKE。
- hybrid_search: RRF 融合两路候选 -> reranker 重排 -> 取最终 top_k，
                 并返回可溯源的结构（document 名、chunk_index、各阶段分数）。

所有原始 SQL 通过 sqlalchemy.text 参数化执行，避免注入。
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.services.reranker import Reranker


def _to_vector_literal(embedding: list[float]) -> str:
    """把 Python float 列表转为 pgvector 文本字面量 '[a,b,c]'。

    以字符串参数 + ::vector 强制转换传入，兼容未注册适配器的连接。
    """
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


class RetrievalService:
    """围绕单个 AsyncSession 的检索门面。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.reranker = Reranker()

    # ------------------------------------------------------ 向量检索（8.2.3）
    async def vector_search(
        self, kb_id: uuid.UUID, query_embedding: list[float], k: int
    ) -> list[dict]:
        """pgvector 余弦距离 top-k：距离越小越相似，转换为相似度分。"""
        qvec = _to_vector_literal(query_embedding)
        # embedding <=> :qvec 为余弦距离 [0,2]，相似度 = 1 - 距离
        sql = text(
            """
            SELECT c.id AS chunk_id,
                   c.document_id AS document_id,
                   c.chunk_index AS chunk_index,
                   c.content AS content,
                   d.file_name AS document,
                   (c.embedding <=> CAST(:qvec AS vector)) AS distance
            FROM documentchunk c
            JOIN document d ON d.id = c.document_id
            WHERE c.knowledge_base_id = :kb_id
              AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> CAST(:qvec AS vector) ASC
            LIMIT :k
            """
        )
        result = await self.session.exec(
            sql, params={"qvec": qvec, "kb_id": str(kb_id), "k": k}
        )
        rows = result.mappings().all()
        out: list[dict] = []
        for r in rows:
            distance = float(r["distance"]) if r["distance"] is not None else 2.0
            out.append(
                {
                    "chunk_id": str(r["chunk_id"]),
                    "document_id": str(r["document_id"]),
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "document": r["document"],
                    "vector_score": 1.0 - distance,
                }
            )
        return out

    # -------------------------------------------------------- BM25（8.2.3）
    async def bm25_search(
        self, kb_id: uuid.UUID, query: str, k: int
    ) -> list[dict]:
        """PG 全文检索 ts_rank top-k；异常或无命中时降级多关键词 ILIKE。"""
        # 多取候选用于后续 Q 行重排
        fetch_k = max(k * 5, 20)
        sql = text(
            """
            SELECT c.id AS chunk_id,
                   c.document_id AS document_id,
                   c.chunk_index AS chunk_index,
                   c.content AS content,
                   d.file_name AS document,
                   ts_rank(c.tsv, plainto_tsquery('simple', :q)) AS rank
            FROM documentchunk c
            JOIN document d ON d.id = c.document_id
            WHERE c.knowledge_base_id = :kb_id
              AND c.tsv @@ plainto_tsquery('simple', :q)
            ORDER BY rank DESC
            LIMIT :k
            """
        )
        try:
            result = await self.session.exec(
                sql, params={"q": query, "kb_id": str(kb_id), "k": fetch_k}
            )
            rows = result.mappings().all()
        except Exception:
            rows = []

        if not rows:
            # 降级：中文关键词 ILIKE 匹配，取全部命中后由 Q-line 重排
            import re
            # 按标点拆成语义段，保留 2-4 字短语
            segments = [w for w in re.split(r'[，。？！/、\s\(\)（）]+', query) if len(w) >= 2]
            keywords: list[str] = []
            for seg in segments:
                if len(seg) <= 4:
                    keywords.append(seg)
                else:
                    # 4字滑动窗口
                    for i in range(0, len(seg) - 3):
                        w = seg[i:i+4]
                        if w not in keywords:
                            keywords.append(w)
                    # 也加 2 字窗口保底
                    for i in range(0, len(seg) - 1):
                        w = seg[i:i+2]
                        if w not in keywords:
                            keywords.append(w)
            if not keywords:
                keywords = [query[:10]]
            # 用 SUM(CASE WHEN ... THEN 1 ELSE 0 END) 计算每个 chunk 的命中关键词数，按命中数降序
            score_expr = " + ".join(
                f"CASE WHEN c.content ILIKE :kw{i} THEN 1 ELSE 0 END"
                for i in range(len(keywords))
            )
            conditions = " OR ".join(
                f"c.content ILIKE :kw{i}" for i in range(len(keywords))
            )
            like_sql = text(
                f"""
                SELECT c.id AS chunk_id,
                       c.document_id AS document_id,
                       c.chunk_index AS chunk_index,
                       c.content AS content,
                       d.file_name AS document,
                       ({score_expr}) AS rank
                FROM documentchunk c
                JOIN document d ON d.id = c.document_id
                WHERE c.knowledge_base_id = :kb_id
                  AND ({conditions})
                ORDER BY rank DESC
                LIMIT 100
                """
            )
            try:
                params: dict = {"kb_id": str(kb_id)}
                for i, kw in enumerate(keywords):
                    params[f"kw{i}"] = f"%{kw}%"
                result = await self.session.exec(like_sql, params=params)
                rows = result.mappings().all()
            except Exception:
                rows = []

        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "chunk_id": str(r["chunk_id"]),
                    "document_id": str(r["document_id"]),
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "document": r["document"],
                    "bm25_score": float(r["rank"]) if r["rank"] is not None else 0.0,
                }
            )

        # 对 QA 格式 chunk 做 Q 行精准重排：query 关键词在 Q 行的命中数优先
        if out:
            import re as _re
            # 提取有意义的短语片段（3-6字滑动窗口）
            clean_q = _re.sub(r'[，。？！/、\s\(\)（）]', '', query)
            phrases = []
            for length in range(min(6, len(clean_q)), 2, -1):
                for i in range(len(clean_q) - length + 1):
                    p = clean_q[i:i+length]
                    if p not in phrases:
                        phrases.append(p)

            def _q_line_score(content: str) -> float:
                m = _re.search(r'Q[：:]\s*(.+?)(?:\n|A[：:])', content, _re.DOTALL)
                q_line = m.group(1) if m else ""
                # 清洗 Q 行标点后匹配
                q_clean = _re.sub(r'[，。？！/、\s\(\)（）]', '', q_line)
                score = 0.0
                for p in phrases:
                    if p in q_clean:
                        score += len(p)
                return score

            out.sort(key=lambda x: _q_line_score(x["content"]), reverse=True)

        return out[:k]

    # --------------------------------------------------- RRF 融合（8.2.3-4）
    @staticmethod
    def _rrf_fuse(
        vector_hits: list[dict], bm25_hits: list[dict], rrf_k: int
    ) -> dict[str, dict]:
        """倒数排名融合：score = Σ 1/(rrf_k + rank)。

        以 chunk_id 聚合两路命中，合并元数据并累加 RRF 分。
        """
        fused: dict[str, dict] = {}

        def _merge(hits: list[dict], score_key: str) -> None:
            for rank, hit in enumerate(hits):
                cid = hit["chunk_id"]
                entry = fused.get(cid)
                if entry is None:
                    entry = {
                        "chunk_id": cid,
                        "document_id": hit["document_id"],
                        "chunk_index": hit["chunk_index"],
                        "content": hit["content"],
                        "document": hit["document"],
                        "scores": {"vector": 0.0, "bm25": 0.0, "rrf": 0.0},
                    }
                    fused[cid] = entry
                # 保留各路原始分用于可解释性
                if score_key == "vector":
                    entry["scores"]["vector"] = hit.get("vector_score", 0.0)
                else:
                    entry["scores"]["bm25"] = hit.get("bm25_score", 0.0)
                entry["scores"]["rrf"] += 1.0 / (rrf_k + rank + 1)

        _merge(vector_hits, "vector")
        _merge(bm25_hits, "bm25")
        return fused

    async def hybrid_search(
        self,
        kb_id: uuid.UUID,
        query: str,
        query_embedding: list[float],
        top_k: int | None = None,
    ) -> list[dict]:
        """完整混合检索：双路召回 -> RRF 融合 -> rerank -> top_k。

        返回 list[dict]：{chunk_id, document_id, document, chunk_index,
        content, scores:{vector,bm25,rrf,rerank}}。
        """
        top_k = top_k or settings.RETRIEVAL_TOP_K
        candidate_k = settings.RETRIEVAL_CANDIDATE_K

        # 8.2.3 双路召回
        vector_hits = await self.vector_search(kb_id, query_embedding, candidate_k)
        bm25_hits = await self.bm25_search(kb_id, query, candidate_k)

        # 8.2.3 RRF 融合
        fused = self._rrf_fuse(vector_hits, bm25_hits, settings.RRF_K)
        candidates = sorted(
            fused.values(), key=lambda c: c["scores"]["rrf"], reverse=True
        )
        # 仅对融合后的前若干候选 rerank，控制 cross-encoder 计算量
        candidates = candidates[:candidate_k]

        # 8.2.4 rerank（降级时保持 RRF 顺序）
        reranked = await self.reranker.rerank(query, candidates)

        # Q-line 精准重排：对 QA 格式 chunk，用 query 短语在 Q 行的命中程度重排
        import re as _re
        clean_q = _re.sub(r'[，。？！/、\s\(\)（）]', '', query)
        phrases = []
        for length in range(min(6, len(clean_q)), 2, -1):
            for i in range(len(clean_q) - length + 1):
                p = clean_q[i:i+length]
                if p not in phrases:
                    phrases.append(p)

        def _q_score(content: str) -> float:
            m = _re.search(r'Q[：:]\s*(.+?)(?:\n|A[：:])', content, _re.DOTALL)
            q_line = m.group(1) if m else ""
            q_clean = _re.sub(r'[，。？！/、\s\(\)（）]', '', q_line)
            return sum(len(p) for p in phrases if p in q_clean)

        reranked.sort(key=lambda x: _q_score(x["content"]), reverse=True)
        return reranked[:top_k]
