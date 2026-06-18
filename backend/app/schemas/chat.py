"""问答接口请求/响应 Schema（设计文档 7.3）。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """RAG 问答请求体（设计文档 7.3）。"""

    query: str = Field(min_length=1)
    top_k: int = Field(default=4, ge=1, le=20)
    use_agent: bool = False
    prompt_version: str | None = None


class Citation(BaseModel):
    """引用溯源条目：定位到文档 + chunk_index。"""

    chunk_id: str
    document: str
    chunk_index: int
    preview: str


class Usage(BaseModel):
    """token / 成本用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0


class AskResponse(BaseModel):
    """RAG 问答响应体（设计文档 7.3）。"""

    answer: str
    is_refused: bool = False
    citations: list[Citation] = []
    retrieval_count: int = 0
    usage: Usage = Usage()
    trace_id: str | None = None
