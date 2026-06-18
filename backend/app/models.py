import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector  # pgvector 向量列类型
from pydantic import EmailStr
from sqlalchemy import Column, DateTime, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlmodel import Field, Relationship, SQLModel

from app.core.config import settings

# embedding 向量维度，集中由配置控制（见设计文档 6.3 document_chunks.embedding vector(N)）
EMBEDDING_DIM = settings.EMBEDDING_DIM


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore[assignment]
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    items: list["Item"] = Relationship(back_populates="owner", cascade_delete=True)
    knowledge_bases: list["KnowledgeBase"]= Relationship(back_populates="owner", cascade_delete=True)
    # 会话与用户绑定，用户删除时级联清理（设计文档 4.1 资源按 user 隔离）
    chat_sessions: list["ChatSession"] = Relationship(
        back_populates="user", cascade_delete=True
    )


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID
    created_at: datetime | None = None


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    pass


# Properties to receive on item update
class ItemUpdate(ItemBase):
    title: str | None = Field(default=None, min_length=1, max_length=255)  # type: ignore[assignment]


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="items")


# Properties to return via API, id is always required
class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class KnowledgeBaseBase(SQLModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


class KnowledgeBase(KnowledgeBaseBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    owner: User | None = Relationship(back_populates="knowledge_bases")
    documents: list["Document"] = Relationship(back_populates="knowledge_base", cascade_delete=True)
    # 会话挂在知识库下（设计文档 6.4 chat_sessions.knowledge_base_id）
    chat_sessions: list["ChatSession"] = Relationship(
        back_populates="knowledge_base", cascade_delete=True
    )


class KnowledgeBaseCreate(KnowledgeBaseBase):
    pass


class KnowledgeBaseUpdate(KnowledgeBaseBase):
    name: str | None = Field(default=None, min_length=1, max_length=255)  # type: ignore[assignment]
    description: str | None = Field(default=None, max_length=1000)


class KnowledgeBasePublic(KnowledgeBaseBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    created_at: datetime | None = None


class KnowledgeBasesPublic(SQLModel):
    data: list[KnowledgeBasePublic]
    count: int


class DocumentBase(SQLModel):
    file_name: str = Field(min_length=1, max_length=255)
    file_type: str = Field(min_length=1, max_length=50)
    content_type: str | None = Field(default=None, max_length=100)
    file_size: int = Field(default=0, ge=0)
    # pending/processing/ready/failed —— 异步文档处理状态机（设计文档 8.1）
    processing_status: str = Field(default="pending", min_length=1, max_length=50)
    # 切分策略：fixed（固定窗口+overlap）/ markdown（标题结构）/ paragraph（段落语义）
    chunk_strategy: str = Field(default="fixed", max_length=32)
    summary: str | None = Field(default=None, max_length=1000)


class Document(DocumentBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    # 文件落本地 volume 的存储路径（设计文档 6.2 storage_path）
    storage_path: str | None = Field(default=None, max_length=512)
    # 处理失败原因（设计文档 6.2 error_message）
    error_message: str | None = Field(default=None, sa_column=Column(Text))
    # 切分得到的 chunk 数量（设计文档 6.2 chunk_count）
    chunk_count: int = Field(default=0, ge=0)
    # 冗余 owner_id，便于按用户直接过滤（与 knowledge_base.owner_id 一致）
    owner_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id", nullable=False, ondelete="CASCADE"
    )
    knowledge_base: KnowledgeBase | None = Relationship(back_populates="documents")
    chunks: list["DocumentChunk"] = Relationship(back_populates="document", cascade_delete=True)


class DocumentCreate(DocumentBase):
    knowledge_base_id: uuid.UUID


class DocumentUpdate(SQLModel):
    file_name: str | None = Field(default=None, min_length=1, max_length=255)
    file_type: str | None = Field(default=None, min_length=1, max_length=50)
    content_type: str | None = Field(default=None, max_length=100)
    file_size: int | None = Field(default=None, ge=0)
    processing_status: str | None = Field(default=None, min_length=1, max_length=50)
    summary: str | None = Field(default=None, max_length=1000)


class DocumentPublic(DocumentBase):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentsPublic(SQLModel):
    data: list[DocumentPublic]
    count: int


class DocumentChunkBase(SQLModel):
    chunk_index: int = Field(ge=0)
    content: str = Field(min_length=1)
    char_count: int = Field(default=0, ge=0)


class DocumentChunk(DocumentChunkBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    # token 数（设计文档 6.3 token_count）
    token_count: int | None = Field(default=None, ge=0)
    # pgvector 向量列：余弦/内积检索的核心（设计文档 6.3 embedding vector(N)）
    embedding: Any | None = Field(
        default=None, sa_column=Column(Vector(EMBEDDING_DIM), nullable=True)
    )
    # 全文检索列，承载 BM25/PG 全文检索（设计文档 6.3 tsv tsvector）
    tsv: Any | None = Field(
        default=None, sa_column=Column(TSVECTOR, nullable=True)
    )
    document_id: uuid.UUID = Field(
        foreign_key="document.id", nullable=False, ondelete="CASCADE"
    )
    # 冗余 knowledge_base_id，便于检索时按知识库过滤（设计文档 6.3 + 18.1 分区/分片键）
    knowledge_base_id: uuid.UUID | None = Field(
        default=None, foreign_key="knowledgebase.id", index=True, ondelete="CASCADE"
    )
    # 冗余 owner_id，便于按用户隔离（与现有 crud.create_documentchunk 一致）
    owner_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="CASCADE"
    )
    document: Document | None = Relationship(back_populates="chunks")


class DocumentChunkCreate(DocumentChunkBase):
    document_id: uuid.UUID


class DocumentChunkUpdate(SQLModel):
    chunk_index: int | None = Field(default=None, ge=0)
    content: str | None = Field(default=None, min_length=1)
    char_count: int | None = Field(default=None, ge=0)


class DocumentChunkPublic(DocumentChunkBase):
    id: uuid.UUID
    document_id: uuid.UUID
    created_at: datetime | None = None


class DocumentChunksPublic(SQLModel):
    data: list[DocumentChunkPublic]
    count: int

class ChatSessionBase(SQLModel):
    title: str | None = Field(default=None, max_length=255)


class ChatSession(ChatSessionBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    updated_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    user_id: uuid.UUID = Field(
        foreign_key="user.id", nullable=False, ondelete="CASCADE"
    )
    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id", nullable=False, ondelete="CASCADE"
    )
    user: User | None = Relationship(back_populates="chat_sessions")
    knowledge_base: KnowledgeBase | None = Relationship(back_populates="chat_sessions")
    # 会话下的消息（设计文档 6.5 chat_messages）
    messages: list["ChatMessage"] = Relationship(
        back_populates="session", cascade_delete=True
    )


class ChatSessionCreate(SQLModel):
    title: str | None = Field(default=None, max_length=255)
    knowledge_base_id: uuid.UUID


class ChatSessionUpdate(SQLModel):
    title: str | None = Field(default=None, max_length=255)


class ChatSessionPublic(ChatSessionBase):
    id: uuid.UUID
    user_id: uuid.UUID
    knowledge_base_id: uuid.UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChatSessionsPublic(SQLModel):
    data: list[ChatSessionPublic]
    count: int


# ---------------------------------------------------------------------------
# 设计文档 6.5 chat_messages：会话消息（user/assistant/system/tool）
# ---------------------------------------------------------------------------
class ChatMessageBase(SQLModel):
    role: str = Field(max_length=32)  # user / assistant / system / tool
    content: str = Field(sa_column=Column(Text))


class ChatMessage(ChatMessageBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )
    model_name: str | None = Field(default=None, max_length=128)
    # 记录所用 prompt 版本，便于 A/B 与效果归因（设计文档 4.6）
    prompt_version: str | None = Field(default=None, max_length=64)
    session_id: uuid.UUID = Field(
        foreign_key="chatsession.id", nullable=False, ondelete="CASCADE", index=True
    )
    session: ChatSession | None = Relationship(back_populates="messages")


class ChatMessagePublic(ChatMessageBase):
    id: uuid.UUID
    session_id: uuid.UUID
    model_name: str | None = None
    prompt_version: str | None = None
    created_at: datetime | None = None


class ChatMessagesPublic(SQLModel):
    data: list[ChatMessagePublic]
    count: int


# ---------------------------------------------------------------------------
# 设计文档 6.6 retrieval_logs（增强）：检索可解释性日志
# ---------------------------------------------------------------------------
class RetrievalLog(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID | None = Field(
        default=None, foreign_key="chatsession.id", ondelete="CASCADE", index=True
    )
    query_text: str = Field(sa_column=Column(Text))
    rewritten_query: str | None = Field(default=None, sa_column=Column(Text))
    # 召回 chunk id 列表（JSONB）
    retrieved_chunk_ids: Any | None = Field(default=None, sa_column=Column(JSONB))
    # 各阶段分数：向量/BM25/rerank（JSONB）
    scores: Any | None = Field(default=None, sa_column=Column(JSONB))
    top_k: int = Field(default=0)
    is_refused: bool = Field(default=False)  # 是否触发拒答
    latency_ms: int = Field(default=0)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# ---------------------------------------------------------------------------
# 设计文档 6.7 token_usages（新增）：token/成本统计
# ---------------------------------------------------------------------------
class TokenUsage(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID | None = Field(
        default=None, foreign_key="chatsession.id", ondelete="CASCADE", index=True
    )
    user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="CASCADE", index=True
    )
    model_name: str = Field(max_length=128)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    # 估算成本，按 provider 维度可聚合（设计文档 18.2 成本路由度量）
    estimated_cost: Decimal = Field(
        default=Decimal("0"), sa_column=Column(Numeric(12, 6))
    )
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


# ---------------------------------------------------------------------------
# 设计文档 6.8 prompt_configs（启用）：可版本化、可热切换的 Prompt
# ---------------------------------------------------------------------------
class PromptConfigBase(SQLModel):
    name: str = Field(max_length=128)
    version: str = Field(max_length=64)
    system_prompt: str = Field(sa_column=Column(Text))
    retrieval_template: str | None = Field(default=None, sa_column=Column(Text))
    answer_template: str | None = Field(default=None, sa_column=Column(Text))
    is_active: bool = Field(default=False)  # 同 name 仅一个 active


class PromptConfig(PromptConfigBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore
    )


class PromptConfigCreate(PromptConfigBase):
    pass


class PromptConfigPublic(PromptConfigBase):
    id: uuid.UUID
    created_at: datetime | None = None


class PromptConfigsPublic(SQLModel):
    data: list[PromptConfigPublic]
    count: int