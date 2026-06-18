"""enable pgvector + v2 AI tables (chunks vector/tsv, chat messages, logs, usage, prompts)

Revision ID: a1b2c3d4e5f6
Revises: 3b94abeedff0
Create Date: 2026-06-18 01:30:00.000000

设计文档 v2 第 6 章数据库设计落地：
- 启用 pgvector 扩展；
- document 增加异步状态机/存储/owner 字段；
- documentchunk 增加 token_count / embedding(vector) / tsv(tsvector) / 冗余外键；
- 新建 chatsession / chatmessage / retrievallog / tokenusage / promptconfig 表；
- 建立 ivfflat 向量索引 + GIN 全文索引（设计文档 18.1 单机索引优化）。
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "3b94abeedff0"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1536  # 需与 settings.EMBEDDING_DIM / models.EMBEDDING_DIM 一致


def upgrade():
    # 1) 启用 pgvector 扩展（向量检索前置条件，设计文档 15 风险：Day1 优先切库）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2) document 表补充字段（设计文档 6.2）
    op.add_column("document", sa.Column("chunk_strategy", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False, server_default="fixed"))
    op.add_column("document", sa.Column("storage_path", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=True))
    op.add_column("document", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("document", sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("document", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_document_owner", "document", "user", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_document_processing_status", "document", ["processing_status"])

    # 3) documentchunk 表补充字段（设计文档 6.3）
    op.add_column("documentchunk", sa.Column("token_count", sa.Integer(), nullable=True))
    op.add_column("documentchunk", sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True))
    op.add_column("documentchunk", sa.Column("tsv", postgresql.TSVECTOR(), nullable=True))
    op.add_column("documentchunk", sa.Column("knowledge_base_id", sa.Uuid(), nullable=True))
    op.add_column("documentchunk", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_chunk_kb", "documentchunk", "knowledgebase", ["knowledge_base_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_chunk_owner", "documentchunk", "user", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_documentchunk_knowledge_base_id", "documentchunk", ["knowledge_base_id"])
    # ivfflat 余弦向量索引（设计文档 18.1：单机索引优化，向量列检索加速）
    op.execute(
        "CREATE INDEX ix_documentchunk_embedding ON documentchunk "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    # GIN 全文索引，支撑 BM25/全文检索（设计文档 6.3 tsv）
    op.execute("CREATE INDEX ix_documentchunk_tsv ON documentchunk USING gin (tsv)")

    # 4) chatsession（设计文档 6.4，模型已定义但此前无迁移）
    op.create_table(
        "chatsession",
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledgebase.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5) chatmessage（设计文档 6.5）
    op.create_table(
        "chatmessage",
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("prompt_version", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chatsession.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chatmessage_session_id", "chatmessage", ["session_id"])

    # 6) retrievallog（设计文档 6.6）
    op.create_table(
        "retrievallog",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("rewritten_query", sa.Text(), nullable=True),
        sa.Column("retrieved_chunk_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_refused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["chatsession.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrievallog_session_id", "retrievallog", ["session_id"])

    # 7) tokenusage（设计文档 6.7）
    op.create_table(
        "tokenusage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("model_name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["chatsession.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tokenusage_session_id", "tokenusage", ["session_id"])
    op.create_index("ix_tokenusage_user_id", "tokenusage", ["user_id"])

    # 8) promptconfig（设计文档 6.8）
    op.create_table(
        "promptconfig",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
        sa.Column("version", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("retrieval_template", sa.Text(), nullable=True),
        sa.Column("answer_template", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("promptconfig")
    op.drop_index("ix_tokenusage_user_id", table_name="tokenusage")
    op.drop_index("ix_tokenusage_session_id", table_name="tokenusage")
    op.drop_table("tokenusage")
    op.drop_index("ix_retrievallog_session_id", table_name="retrievallog")
    op.drop_table("retrievallog")
    op.drop_index("ix_chatmessage_session_id", table_name="chatmessage")
    op.drop_table("chatmessage")
    op.drop_table("chatsession")

    op.drop_index("ix_documentchunk_tsv", table_name="documentchunk")
    op.drop_index("ix_documentchunk_embedding", table_name="documentchunk")
    op.drop_index("ix_documentchunk_knowledge_base_id", table_name="documentchunk")
    op.drop_constraint("fk_chunk_owner", "documentchunk", type_="foreignkey")
    op.drop_constraint("fk_chunk_kb", "documentchunk", type_="foreignkey")
    op.drop_column("documentchunk", "owner_id")
    op.drop_column("documentchunk", "knowledge_base_id")
    op.drop_column("documentchunk", "tsv")
    op.drop_column("documentchunk", "embedding")
    op.drop_column("documentchunk", "token_count")

    op.drop_index("ix_document_processing_status", table_name="document")
    op.drop_constraint("fk_document_owner", "document", type_="foreignkey")
    op.drop_column("document", "owner_id")
    op.drop_column("document", "chunk_count")
    op.drop_column("document", "error_message")
    op.drop_column("document", "storage_path")
    op.drop_column("document", "chunk_strategy")
