import uuid
from typing import Any

from fastapi import HTTPException
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import get_password_hash, verify_password
from app.models import (
    Document,
    DocumentChunk,
    DocumentChunkCreate,
    DocumentChunkUpdate,
    DocumentCreate,
    DocumentUpdate,
    Item,
    ItemCreate,
    KnowledgeBase,
    KnowledgeBaseCreate,
    User,
    UserCreate,
    UserUpdate,
)


async def create_user(*, session: AsyncSession, user_create: UserCreate) -> User:
    db_obj = User.model_validate(
        user_create, update={"hashed_password": get_password_hash(user_create.password)}
    )
    session.add(db_obj)
    await session.commit()
    await session.refresh(db_obj)
    return db_obj


async def update_user(*, session: AsyncSession, db_user: User, user_in: UserUpdate) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    return db_user


async def get_user_by_email(*, session: AsyncSession, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    result = await session.exec(statement)
    return result.first()


# Dummy hash to use for timing attack prevention when user is not found
# This is an Argon2 hash of a random password, used to ensure constant-time comparison
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"


async def authenticate(*, session: AsyncSession, email: str, password: str) -> User | None:
    db_user = await get_user_by_email(session=session, email=email)
    if not db_user:
        # Prevent timing attacks by running password verification even when user doesn't exist
        # This ensures the response time is similar whether or not the email exists
        verify_password(password, DUMMY_HASH)
        return None
    verified, updated_password_hash = verify_password(password, db_user.hashed_password)
    if not verified:
        return None
    if updated_password_hash:
        db_user.hashed_password = updated_password_hash
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
    return db_user


async def create_item(*, session: AsyncSession, item_in: ItemCreate, owner_id: uuid.UUID) -> Item:
    db_item = Item.model_validate(item_in, update={"owner_id": owner_id})
    session.add(db_item)
    await session.commit()
    await session.refresh(db_item)
    return db_item


async def create_knowledge_base(
    *, session: AsyncSession, kb_in: KnowledgeBaseCreate, owner_id: uuid.UUID
) -> KnowledgeBase:
    db_kb = KnowledgeBase.model_validate(kb_in, update={"owner_id": owner_id})
    session.add(db_kb)
    await session.commit()
    await session.refresh(db_kb)
    return db_kb


async def get_current_user_knowledgebases(
    *,
    session: AsyncSession,
    current_user: User,
    limit: int,
    offset: int,
) -> tuple[list[KnowledgeBase], int]:
    base_stmt = select(KnowledgeBase).where(KnowledgeBase.owner_id == current_user.id)
    kb_result = await session.exec(base_stmt.offset(offset).limit(limit))
    kb_list = kb_result.all()

    count_stmt = (
        select(func.count())
        .select_from(KnowledgeBase)
        .where(KnowledgeBase.owner_id == current_user.id)
    )
    count_result = await session.exec(count_stmt)
    total = count_result.one()

    return kb_list, total

async def update_knowledge_base(
    *, session: AsyncSession, db_kb: KnowledgeBase, kb_in: KnowledgeBaseCreate
) -> KnowledgeBase:
    kb_data = kb_in.model_dump(exclude_unset=True)
    db_kb.sqlmodel_update(kb_data)
    session.add(db_kb)
    await session.commit()
    await session.refresh(db_kb)
    return db_kb

async def delete_knowledge_base(
    *, session: AsyncSession, kb_id: uuid.UUID, owner_id: uuid.UUID
) -> None:
    statement = select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.owner_id == owner_id)
    result = await session.exec(statement)
    db_kb = result.first()
    if not db_kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    await session.delete(db_kb)
    await session.commit()


async def create_document(
    *, session: AsyncSession, document_in: DocumentCreate, owner_id: uuid.UUID
) -> Document:
    db_document = Document.model_validate(document_in, update={"owner_id": owner_id})
    session.add(db_document)
    await session.commit()
    await session.refresh(db_document)
    return db_document

async def delete_document(
    *, session: AsyncSession, document_id: uuid.UUID, owner_id: uuid.UUID
) -> None:
    statement = select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
    result = await session.exec(statement)
    db_document = result.first()
    if not db_document:
        raise HTTPException(status_code=404, detail="Document not found")
    await session.delete(db_document)
    await session.commit()

async def update_document(
    *, session: AsyncSession, db_document: Document, document_in: DocumentUpdate
) -> Document:
    document_data = document_in.model_dump(exclude_unset=True)
    db_document.sqlmodel_update(document_data)
    session.add(db_document)
    await session.commit()
    await session.refresh(db_document)
    return db_document


async def get_current_user_documents(
    *,
    session: AsyncSession,
    current_user: User,
    limit: int,
    offset: int,
) -> tuple[list[Document], int]:

    base_stmt = select(Document).where(Document.owner_id == current_user.id)
    document_result = await session.exec(base_stmt.offset(offset).limit(limit))
    document_list = document_result.all()

    count_stmt = (
        select(func.count())
        .select_from(Document)
        .where(Document.owner_id == current_user.id)
    )
    count_result = await session.exec(count_stmt)
    total = count_result.one()

    return document_list, total


async def create_documentchunk(
    *,session:AsyncSession, chunk_in: DocumentChunkCreate, owner_id: uuid.UUID
) -> DocumentChunk:
    db_chunk = DocumentChunk.model_validate(chunk_in, update={"owner_id": owner_id})
    session.add(db_chunk)
    await session.commit()
    await session.refresh(db_chunk)
    return db_chunk

async def create_documentchunks(
    *,session:AsyncSession, chunks_in: list[DocumentChunkCreate], owner_id: uuid.UUID
) -> list[DocumentChunk]:
    db_chunks = [DocumentChunk.model_validate(chunk_in, update={"owner_id": owner_id}) for chunk_in in chunks_in]
    session.add_all(db_chunks)
    await session.commit()
    for chunk in db_chunks:
        await session.refresh(chunk)
    return db_chunks

async def delete_documentchunk(
    *, session: AsyncSession, db_chunk: DocumentChunk
) -> None:
    await session.delete(db_chunk)
    await session.commit()

async def delete_documentchunks(
    *, session: AsyncSession, document_id: uuid.UUID
) -> None:
    statement = select(DocumentChunk).where(DocumentChunk.document_id == document_id)
    result = await session.exec(statement)
    db_chunks = result.all()
    count=len(db_chunks)
    for chunk in db_chunks:
        await session.delete(chunk)
    await session.commit()
    return count

async def update_documentchunk(
    *, session: AsyncSession, db_chunk: DocumentChunk, chunk_in: DocumentChunkUpdate
) -> DocumentChunk:
    chunk_data=chunk_in.model_dump(exclude_unset=True)
    db_chunk.sqlmodel_update(chunk_data)
    session.add(db_chunk)
    await session.commit()
    await session.refresh(db_chunk)
    return db_chunk

async def get_documentchunk(*,session:AsyncSession, chunk_id: uuid.UUID) -> DocumentChunk | None:
    return await session.get(DocumentChunk, chunk_id)

async def get_documentchunks(*,session:AsyncSession, document_id: uuid.UUID,skip: int=0,limit: int=100) -> tuple[list[DocumentChunk], int]:
    count_stmt = select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == document_id)
    count_result = await session.exec(count_stmt)
    total = count_result.one()

    stmt=(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .offset(skip)
        .limit(limit)
    )
    result = await session.exec(stmt)
    chunks=result.all()
    return chunks, total



    