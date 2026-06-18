from typing import Any
import uuid

from fastapi import APIRouter, HTTPException
from sqlmodel import func, select

from app.api.deps import CurrentUser, SessionDep
from app import crud
from app.models import (
    Document,
    DocumentChunk,
    DocumentChunkCreate,
    DocumentChunkUpdate,
    KnowledgeBase,
    Message,
)

router = APIRouter(prefix="/document-chunks", tags=["document-chunks"])


@router.get("/", response_model=dict)
async def read_chunks(
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> Any:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    chunks, total = await crud.get_documentchunks(
        session=session, document_id=document_id, skip=skip, limit=limit
    )
    return {"data": chunks, "count": total}


@router.get("/{id}")
async def read_chunk(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Any:
    chunk = await crud.get_documentchunk(session=session, chunk_id=id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Document chunk not found")
    document = await session.get(Document, chunk.document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")
    return chunk


@router.post("/")
async def create_chunk(
    *, session: SessionDep, current_user: CurrentUser, chunk_in: DocumentChunkCreate
) -> Any:
    document = await session.get(Document, chunk_in.document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    chunk = await crud.create_documentchunk(session=session, chunk_in=chunk_in)
    return chunk


@router.post("/bulk")
async def create_chunks_bulk(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    document_id: uuid.UUID,
    chunks_in: list[DocumentChunkCreate],
) -> Any:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    for c in chunks_in:
        if c.document_id != document_id:
            raise HTTPException(
                status_code=400,
                detail="All chunks must belong to the specified document",
            )

    chunks = await crud.create_documentchunks(session=session, chunks_in=chunks_in)
    return {"data": chunks, "count": len(chunks)}


@router.put("/{id}")
async def update_chunk(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    chunk_in: DocumentChunkUpdate,
) -> Any:
    db_chunk = await crud.get_documentchunk(session=session, chunk_id=id)
    if not db_chunk:
        raise HTTPException(status_code=404, detail="Document chunk not found")
    document = await session.get(Document, db_chunk.document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    updated = await crud.update_documentchunk(
        session=session, db_chunk=db_chunk, chunk_in=chunk_in
    )
    return updated


@router.delete("/{id}")
async def delete_chunk(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    db_chunk = await crud.get_documentchunk(session=session, chunk_id=id)
    if not db_chunk:
        raise HTTPException(status_code=404, detail="Document chunk not found")
    document = await session.get(Document, db_chunk.document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    await crud.delete_documentchunk(session=session, db_chunk=db_chunk)
    return Message(message="Document chunk deleted successfully")


@router.delete("/by-document/{document_id}")
async def delete_chunks_by_document(
    session: SessionDep, current_user: CurrentUser, document_id: uuid.UUID
) -> Message:
    document = await session.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")

    count = await crud.delete_documentchunks(
        session=session, document_id=document_id
    )
    return Message(message=f"{count} chunks deleted successfully")
