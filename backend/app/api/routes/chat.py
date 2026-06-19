"""会话与问答接口（设计文档 7.3 / 8.2）。

POST   /chat/sessions                  创建会话（校验知识库归属）
GET    /chat/sessions                  列出当前用户会话
GET    /chat/sessions/{id}/messages    会话历史
POST   /chat/sessions/{id}/ask         RAG 主问答（非流式）
POST   /chat/sessions/{id}/ask/stream  SSE 流式问答

所有会话操作均校验归属当前用户；ask 接口前置按用户限流（设计文档 4.1/11.2）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import func, select

from app.api.deps import CurrentUser, SessionDep
from app.core.rate_limit import check_rate_limit
from app.models import (
    ChatMessage,
    ChatMessagePublic,
    ChatMessagesPublic,
    ChatSession,
    ChatSessionCreate,
    ChatSessionPublic,
    ChatSessionsPublic,
    KnowledgeBase,
)
from app.schemas.chat import AskRequest, AskResponse
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


async def _get_owned_session(
    session: SessionDep, current_user: CurrentUser, session_id: uuid.UUID
) -> ChatSession:
    """加载会话并校验归属当前用户。"""
    chat_session = await session.get(ChatSession, session_id)
    if not chat_session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    if chat_session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return chat_session


@router.post("/sessions", response_model=ChatSessionPublic)
async def create_session(
    *, session: SessionDep, current_user: CurrentUser, session_in: ChatSessionCreate
) -> Any:
    """创建会话：校验知识库存在且归属当前用户。"""
    kb = await session.get(KnowledgeBase, session_in.knowledge_base_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if not current_user.is_superuser and kb.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    chat_session = ChatSession.model_validate(
        session_in, update={"user_id": current_user.id}
    )
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)
    return chat_session


@router.get("/sessions", response_model=ChatSessionsPublic)
async def list_sessions(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    """列出当前用户的会话（按更新时间倒序）。"""
    count_stmt = (
        select(func.count())
        .select_from(ChatSession)
        .where(ChatSession.user_id == current_user.id)
    )
    count = (await session.exec(count_stmt)).one()
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.exec(stmt)
    data = [ChatSessionPublic.model_validate(s) for s in result.all()]
    return ChatSessionsPublic(data=data, count=count)


@router.get("/sessions/{id}/messages", response_model=ChatMessagesPublic)
async def list_messages(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Any:
    """获取会话历史消息（按时间升序）。"""
    await _get_owned_session(session, current_user, id)
    count_stmt = (
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.session_id == id)
    )
    count = (await session.exec(count_stmt)).one()
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == id)
        .order_by(ChatMessage.created_at)
    )
    result = await session.exec(stmt)
    data = [ChatMessagePublic.model_validate(m) for m in result.all()]
    return ChatMessagesPublic(data=data, count=count)


async def _check_rate_limit_or_429(current_user: CurrentUser) -> None:
    allowed, _ = await check_rate_limit(str(current_user.id))
    if not allowed:
        raise HTTPException(
            status_code=429, detail="Rate limit exceeded, please retry later"
        )


@router.post("/sessions/{id}/ask", response_model=AskResponse)
async def ask(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    ask_in: AskRequest,
) -> Any:
    """RAG 主问答（非流式，设计文档 8.2）。"""
    chat_session = await _get_owned_session(session, current_user, id)
    await _check_rate_limit_or_429(current_user)

    # use_agent=True 时委托 AgentService（ReAct 工具循环，设计文档 8.3）；
    # 不可用则降级普通 RAG。AgentService.run(query, kb_id) 返回
    # {answer, steps, citations, usage} —— 适配为 AskResponse 形状。
    if ask_in.use_agent:
        try:
            from app.services.agent_service import AgentService  # type: ignore

            agent = AgentService(session)
            agent_result = await agent.run(
                ask_in.query, kb_id=str(chat_session.knowledge_base_id)
            )
            usage = agent_result.get("usage") or {}
            # AgentService 的 citation 形状与 RAG 略有差异，归一化到 Citation schema
            citations = [
                {
                    "chunk_id": str(c.get("chunk_id") or ""),
                    "document": str(c.get("document") or c.get("document_id") or ""),
                    "chunk_index": int(c.get("chunk_index") or 0),
                    "preview": c.get("preview") or "",
                }
                for c in (agent_result.get("citations") or [])
            ]
            return {
                "answer": agent_result.get("answer", ""),
                "is_refused": False,
                "citations": citations,
                "retrieval_count": len(citations),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "estimated_cost": usage.get("estimated_cost", 0.0),
                },
                "trace_id": None,
            }
        except Exception as exc:  # noqa: BLE001
            # agent_service / LLMService 任何异常 -> 回退普通 RAG，避免 500
            import logging

            logging.getLogger(__name__).warning(
                "Agent run failed, fallback to plain RAG: %s", exc
            )

    service = ChatService(session)
    return await service.ask(
        chat_session,
        ask_in.query,
        top_k=ask_in.top_k,
        prompt_version=ask_in.prompt_version,
    )


def _sse_format(event: str, data: dict) -> str:
    """编码为 SSE 帧：event: <name>\\ndata: <json>\\n\\n。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/sessions/{id}/ask/stream")
async def ask_stream(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    ask_in: AskRequest,
) -> StreamingResponse:
    """SSE 流式问答（设计文档 7.3 事件序列：retrieval -> token* -> done）。"""
    chat_session = await _get_owned_session(session, current_user, id)
    await _check_rate_limit_or_429(current_user)

    service = ChatService(session)

    async def event_generator() -> Any:
        async for evt in service.ask_stream(
            chat_session,
            ask_in.query,
            top_k=ask_in.top_k,
            prompt_version=ask_in.prompt_version,
        ):
            yield _sse_format(evt["event"], evt["data"])

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        event_generator(), media_type="text/event-stream", headers=headers
    )
