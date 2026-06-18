from typing import Any
import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from sqlmodel import func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Document,
    DocumentCreate,
    DocumentPublic,
    DocumentsPublic,
    DocumentUpdate,
    KnowledgeBase,
    Message,
)

from app.workers.document_tasks import process_document

router = APIRouter(prefix="/documents", tags=["documents"])

UPLOAD_DIR = Path("/app/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/", response_model=DocumentsPublic)
async def read_documents(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
    knowledge_base_id: uuid.UUID | None = None,
) -> Any:
    if current_user.is_superuser:
        count_statement = select(func.count()).select_from(Document)
        statement = select(Document)
        if knowledge_base_id:
            count_statement = count_statement.where(
                Document.knowledge_base_id == knowledge_base_id
            )
            statement = statement.where(
                Document.knowledge_base_id == knowledge_base_id
            )
    else:
        count_statement = (
            select(func.count())
            .select_from(Document)
            .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
            .where(KnowledgeBase.owner_id == current_user.id)
        )
        statement = (
            select(Document)
            .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
            .where(KnowledgeBase.owner_id == current_user.id)
        )
        if knowledge_base_id:
            count_statement = count_statement.where(
                Document.knowledge_base_id == knowledge_base_id
            )
            statement = statement.where(
                Document.knowledge_base_id == knowledge_base_id
            )

    count_result = await session.exec(count_statement)
    count = count_result.one()
    documents_result = await session.exec(statement.offset(skip).limit(limit))
    documents = documents_result.all()
    return DocumentsPublic(
        data=[DocumentPublic.model_validate(d) for d in documents], count=count
    )


@router.get("/{id}", response_model=DocumentPublic)
async def read_document(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Any:
    document = await session.get(Document, id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")
    return document


@router.post("/", response_model=DocumentPublic)
async def create_document(
    *, session: SessionDep, current_user: CurrentUser, document_in: DocumentCreate
) -> Any:
    kb = await session.get(KnowledgeBase, document_in.knowledge_base_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if not current_user.is_superuser and kb.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    document = Document.model_validate(
        document_in, update={"owner_id": kb.owner_id}
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


@router.post("/upload", response_model=DocumentPublic)
async def upload_document(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    knowledge_base_id: uuid.UUID,
    file: UploadFile,
    chunk_strategy: str = "fixed",
) -> Any:
    """上传文件并创建文档记录。文件落盘到 UPLOAD_DIR/{kb_id}/{filename}。"""
    kb = await session.get(KnowledgeBase, knowledge_base_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if not current_user.is_superuser and kb.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    content = await file.read()
    file_size = len(content)

    kb_dir = UPLOAD_DIR / str(knowledge_base_id)
    kb_dir.mkdir(parents=True, exist_ok=True)
    dest = kb_dir / (file.filename or "unnamed")
    dest.write_bytes(content)

    suffix = (file.filename or "").rsplit(".", 1)[-1] if file.filename else "bin"
    document = Document(
        file_name=file.filename or "unnamed",
        file_type=suffix,
        content_type=file.content_type,
        file_size=file_size,
        processing_status="pending",
        chunk_strategy=chunk_strategy,
        storage_path=str(dest),
        owner_id=kb.owner_id,
        knowledge_base_id=knowledge_base_id,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    # 触发后台异步切分 + embedding
    asyncio.create_task(process_document(document.id))
    return document


@router.put("/{id}", response_model=DocumentPublic)
async def update_document(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    document_in: DocumentUpdate,
) -> Any:
    document = await session.get(Document, id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")
    update_dict = document_in.model_dump(exclude_unset=True)
    document.sqlmodel_update(update_dict)
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


@router.delete("/{id}")
async def delete_document(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    document = await session.get(Document, id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")
    await session.delete(document)
    await session.commit()
    return Message(message="Document deleted successfully")


@router.post("/{id}/reprocess", response_model=DocumentPublic)
async def reprocess_document(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Any:
    """重新处理文档：删除旧 chunks 后重新切分 + embedding。"""
    document = await session.get(Document, id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.is_superuser:
        kb = await session.get(KnowledgeBase, document.knowledge_base_id)
        if not kb or kb.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not enough permissions")
    from sqlalchemy import delete as sa_delete
    from app.models import DocumentChunk
    await session.exec(
        sa_delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
    )
    document.processing_status = "pending"
    document.chunk_count = 0
    document.error_message = None
    session.add(document)
    await session.commit()
    await session.refresh(document)
    asyncio.create_task(process_document(document.id))
    return document

