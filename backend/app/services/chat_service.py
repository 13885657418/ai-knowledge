"""RAG 主链路编排（设计文档 8.2）。

ChatService 串起完整问答流程：
1. 加载历史；2. 多轮 query 改写；3. query 向量化；4. 混合检索；
5. 拒答判定（最高分 < 阈值）；6. Prompt 渲染；7. 生成（流式/非流式）；
8. 落库（user/assistant message、retrieval_log、token_usage）+ 可观测上报；
9. 返回 answer + citations + usage（结构对齐设计文档 7.3）。

提供两个入口：
- ask(...)        非流式，返回完整 dict
- ask_stream(...) 异步生成器，产出 SSE 就绪事件 dict（type: retrieval/token/done）
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import AsyncIterator

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models import ChatMessage, ChatSession, RetrievalLog, TokenUsage
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService
from app.services.observability import Observability
from app.services.prompt_service import PromptService
from app.services.retrieval_service import RetrievalService

# 改写时回看的最近历史轮数（设计文档 8.2 步骤 2）
_REWRITE_HISTORY_TURNS = 6
# 注入 LLM 的历史消息条数上限（设计文档 8.2 步骤 6 上下文压缩）
_MAX_HISTORY_MESSAGES = 10


class ChatService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.embedding = EmbeddingService()
        self.llm = LLMService()
        self.retrieval = RetrievalService(session)
        self.prompts = PromptService(session)
        self.observability = Observability()

    # -------------------------------------------------- 8.2.1 历史加载
    async def _load_history(self, session_id: uuid.UUID) -> list[dict]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        result = await self.session.exec(stmt)
        msgs = result.all()
        return [{"role": m.role, "content": m.content} for m in msgs]

    # -------------------------------------------------- 8.2.2 query 改写
    async def _rewrite_query(self, query: str, history: list[dict]) -> str:
        """多轮场景下把指代/省略补全为完整问题；单轮直接返回原 query。"""
        if not history:
            return query
        # mock 模式下跳过改写，避免 mock LLM 回显 prompt 导致 query 被污染
        if (settings.LLM_PROVIDER or "mock").lower() == "mock":
            return query
        recent = history[-_REWRITE_HISTORY_TURNS:]
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
        rewrite_messages = [
            {
                "role": "system",
                "content": (
                    "你是查询改写器。结合对话历史，把用户最新问题改写为"
                    "不依赖上下文、可独立检索的完整问题。只输出改写后的问题，不要解释。"
                ),
            },
            {
                "role": "user",
                "content": f"对话历史：\n{convo}\n\n最新问题：{query}\n\n改写后的问题：",
            },
        ]
        try:
            result = await self.llm.chat(rewrite_messages)
            rewritten = (result.get("content") or "").strip()
            return rewritten or query
        except Exception:
            return query

    # -------------------------------------------------- citations 构建
    @staticmethod
    def _build_citations(chunks: list[dict]) -> list[dict]:
        citations = []
        for c in chunks:
            preview = (c.get("content") or "")[:200]
            citations.append(
                {
                    "chunk_id": c["chunk_id"],
                    "document": c.get("document", ""),
                    "chunk_index": c.get("chunk_index", 0),
                    "preview": preview,
                }
            )
        return citations

    @staticmethod
    def _max_score(chunks: list[dict]) -> float:
        """取候选最高相关度，用于拒答判定（优先 rerank，回退 vector）。"""
        best = 0.0
        for c in chunks:
            scores = c.get("scores", {})
            s = scores.get("rerank")
            if s is None:
                s = scores.get("vector", 0.0) or 0.0
            best = max(best, float(s))
        return best

    # -------------------------------------------------- 落库辅助
    async def _persist_messages(
        self,
        session_id: uuid.UUID,
        user_query: str,
        answer: str,
        model_name: str,
        prompt_version: str | None,
    ) -> None:
        user_msg = ChatMessage(
            session_id=session_id, role="user", content=user_query
        )
        assistant_msg = ChatMessage(
            session_id=session_id,
            role="assistant",
            content=answer,
            model_name=model_name,
            prompt_version=prompt_version,
        )
        self.session.add(user_msg)
        self.session.add(assistant_msg)

    async def _persist_logs(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None,
        query: str,
        rewritten: str,
        chunks: list[dict],
        top_k: int,
        is_refused: bool,
        latency_ms: int,
        usage: dict,
    ) -> None:
        retrieval_log = RetrievalLog(
            session_id=session_id,
            query_text=query,
            rewritten_query=rewritten if rewritten != query else None,
            retrieved_chunk_ids=[c["chunk_id"] for c in chunks],
            scores={c["chunk_id"]: c.get("scores", {}) for c in chunks},
            top_k=top_k,
            is_refused=is_refused,
            latency_ms=latency_ms,
        )
        self.session.add(retrieval_log)
        if usage:
            token_usage = TokenUsage(
                session_id=session_id,
                user_id=user_id,
                model_name=usage.get("model", self.llm.model),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                estimated_cost=Decimal(str(usage.get("estimated_cost", 0.0))),
            )
            self.session.add(token_usage)

    # -------------------------------------------------- 检索阶段（共用）
    async def _retrieve(
        self, kb_id: uuid.UUID, query: str, history: list[dict], top_k: int
    ) -> tuple[str, list[dict]]:
        rewritten = await self._rewrite_query(query, history)
        query_embedding = await self.embedding.embed_query(rewritten)
        chunks = await self.retrieval.hybrid_search(
            kb_id, rewritten, query_embedding, top_k
        )
        return rewritten, chunks

    @staticmethod
    def _usage_public(usage: dict) -> dict:
        return {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "estimated_cost": usage.get("estimated_cost", 0.0),
        }

    # ============================================ 非流式入口 ask（8.2）
    async def ask(
        self,
        chat_session: ChatSession,
        query: str,
        top_k: int | None = None,
        prompt_version: str | None = None,
    ) -> dict:
        """完整 RAG 问答（非流式），返回结构对齐设计文档 7.3。"""
        top_k = top_k or settings.RETRIEVAL_TOP_K
        started = time.monotonic()
        history = await self._load_history(chat_session.id)

        async with self.observability.trace(
            "rag.ask", input={"query": query}
        ) as tracer:
            rewritten, chunks = await self._retrieve(
                chat_session.knowledge_base_id, query, history, top_k
            )
            tracer.event("retrieval", metadata={"count": len(chunks)})

            max_score = self._max_score(chunks)
            # mock 模式下跳过拒答判定（mock 向量无语义，分数不可靠）
            _is_mock = (settings.EMBEDDING_PROVIDER or "mock").lower() == "mock"
            is_refused = (
                False if _is_mock
                else ((not chunks) or (max_score < settings.REFUSAL_THRESHOLD))
            )

            if is_refused:
                answer = "根据现有资料，我无法回答这个问题。"
                usage: dict = {}
                model_name = self.llm.model
            else:
                prompt_config = await self.prompts.get_active("default")
                messages = self.prompts.render(
                    prompt_config,
                    chunks,
                    history[-_MAX_HISTORY_MESSAGES:],
                    rewritten,
                )
                result = await self.llm.chat(messages)
                answer = result["content"]
                usage = result["usage"]
                model_name = usage.get("model", self.llm.model)

            latency_ms = int((time.monotonic() - started) * 1000)

            # 8.2.8 落库 + 上报
            await self._persist_messages(
                chat_session.id, query, answer, model_name, prompt_version
            )
            await self._persist_logs(
                chat_session.id,
                chat_session.user_id,
                query,
                rewritten,
                chunks,
                top_k,
                is_refused,
                latency_ms,
                usage,
            )
            await self.session.commit()
            tracer.update(output={"answer": answer, "is_refused": is_refused})

            return {
                "answer": answer,
                "is_refused": is_refused,
                "citations": self._build_citations(chunks),
                "retrieval_count": len(chunks),
                "usage": self._usage_public(usage),
                "trace_id": tracer.trace_id,
            }

    # ============================================ 流式入口 ask_stream（8.2）
    async def ask_stream(
        self,
        chat_session: ChatSession,
        query: str,
        top_k: int | None = None,
        prompt_version: str | None = None,
    ) -> AsyncIterator[dict]:
        """流式 RAG 问答，产出 SSE 就绪事件 dict：

        - {"event": "retrieval", "data": {...}}  召回元数据
        - {"event": "token", "data": {"token": ...}}  增量文本（多次）
        - {"event": "done", "data": {"citations", "usage", ...}}
        """
        top_k = top_k or settings.RETRIEVAL_TOP_K
        started = time.monotonic()
        history = await self._load_history(chat_session.id)

        async with self.observability.trace(
            "rag.ask_stream", input={"query": query}
        ) as tracer:
            rewritten, chunks = await self._retrieve(
                chat_session.knowledge_base_id, query, history, top_k
            )
            citations = self._build_citations(chunks)

            # 先把召回元数据发给前端（设计文档 7.3 事件序列首帧）
            yield {
                "event": "retrieval",
                "data": {
                    "retrieval_count": len(chunks),
                    "citations": citations,
                    "trace_id": tracer.trace_id,
                },
            }

            max_score = self._max_score(chunks)
            _is_mock = (settings.EMBEDDING_PROVIDER or "mock").lower() == "mock"
            is_refused = (
                False if _is_mock
                else ((not chunks) or (max_score < settings.REFUSAL_THRESHOLD))
            )

            answer_parts: list[str] = []
            if is_refused:
                refusal = "根据现有资料，我无法回答这个问题。"
                answer_parts.append(refusal)
                yield {"event": "token", "data": {"token": refusal}}
                usage: dict = {}
                model_name = self.llm.model
            else:
                prompt_config = await self.prompts.get_active("default")
                messages = self.prompts.render(
                    prompt_config,
                    chunks,
                    history[-_MAX_HISTORY_MESSAGES:],
                    rewritten,
                )
                async for token in self.llm.stream_chat(messages):
                    answer_parts.append(token)
                    yield {"event": "token", "data": {"token": token}}
                answer = "".join(answer_parts)
                # 流式无服务端 usage 回传，按文本估算
                prompt_text = "\n".join(m["content"] for m in messages)
                usage = {
                    "prompt_tokens": self.llm.count_tokens(prompt_text),
                    "completion_tokens": self.llm.count_tokens(answer),
                    "model": self.llm.model,
                }
                usage["total_tokens"] = (
                    usage["prompt_tokens"] + usage["completion_tokens"]
                )
                usage["estimated_cost"] = self.llm.estimate_cost(
                    self.llm.model,
                    usage["prompt_tokens"],
                    usage["completion_tokens"],
                )
                model_name = self.llm.model

            answer = "".join(answer_parts)
            latency_ms = int((time.monotonic() - started) * 1000)

            await self._persist_messages(
                chat_session.id, query, answer, model_name, prompt_version
            )
            await self._persist_logs(
                chat_session.id,
                chat_session.user_id,
                query,
                rewritten,
                chunks,
                top_k,
                is_refused,
                latency_ms,
                usage,
            )
            await self.session.commit()
            tracer.update(output={"answer": answer, "is_refused": is_refused})

            yield {
                "event": "done",
                "data": {
                    "is_refused": is_refused,
                    "citations": citations,
                    "usage": self._usage_public(usage),
                    "trace_id": tracer.trace_id,
                },
            }
