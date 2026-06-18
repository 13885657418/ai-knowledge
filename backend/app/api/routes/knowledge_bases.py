from typing import Any
import uuid

from fastapi import APIRouter, HTTPException
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBasePublic,
    KnowledgeBasesPublic,
    KnowledgeBaseUpdate,
    Message,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.get("/", response_model=KnowledgeBasesPublic)
async def read_knowledge_bases(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    """
    Retrieve knowledge bases.
    """

    if current_user.is_superuser:
        count_statement = select(func.count()).select_from(KnowledgeBase)
        count_result = await session.exec(count_statement)
        count = count_result.one()
        statement = (
            select(KnowledgeBase)
            .order_by(col(KnowledgeBase.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
    else:
        count_statement = (
            select(func.count())
            .select_from(KnowledgeBase)
            .where(KnowledgeBase.owner_id == current_user.id)
        )
        count_result = await session.exec(count_statement)
        count = count_result.one()
        statement = (
            select(KnowledgeBase)
            .where(KnowledgeBase.owner_id == current_user.id)
            .order_by(col(KnowledgeBase.created_at).desc())
            .offset(skip)
            .limit(limit)
        )

    knowledge_bases_result = await session.exec(statement)
    knowledge_bases = knowledge_bases_result.all()
    knowledge_bases_public = [
        KnowledgeBasePublic.model_validate(knowledge_base)
        for knowledge_base in knowledge_bases
    ]
    return KnowledgeBasesPublic(data=knowledge_bases_public, count=count)


@router.get("/{id}", response_model=KnowledgeBasePublic)
async def read_knowledge_base(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Any:
    """
    Get a knowledge base by ID.
    """
    knowledge_base = await session.get(KnowledgeBase, id)
    if not knowledge_base:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if (
        not current_user.is_superuser
        and knowledge_base.owner_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return knowledge_base


@router.post("/", response_model=KnowledgeBasePublic)
async def create_knowledge_base(
    *, session: SessionDep, current_user: CurrentUser, kb_in: KnowledgeBaseCreate
) -> Any:
    """
    Create new knowledge base.
    """
    knowledge_base = KnowledgeBase.model_validate(
        kb_in, update={"owner_id": current_user.id}
    )
    session.add(knowledge_base)
    await session.commit()
    await session.refresh(knowledge_base)
    return knowledge_base


@router.put("/{id}", response_model=KnowledgeBasePublic)
async def update_knowledge_base(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    kb_in: KnowledgeBaseUpdate,
) -> Any:
    """
    Update a knowledge base.
    """
    knowledge_base = await session.get(KnowledgeBase, id)
    if not knowledge_base:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if (
        not current_user.is_superuser
        and knowledge_base.owner_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    update_dict = kb_in.model_dump(exclude_unset=True)
    knowledge_base.sqlmodel_update(update_dict)
    session.add(knowledge_base)
    await session.commit()
    await session.refresh(knowledge_base)
    return knowledge_base


@router.delete("/{id}")
async def delete_knowledge_base(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    """
    Delete a knowledge base.
    """
    knowledge_base = await session.get(KnowledgeBase, id)
    if not knowledge_base:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if (
        not current_user.is_superuser
        and knowledge_base.owner_id != current_user.id
    ):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    await session.delete(knowledge_base)
    await session.commit()
    return Message(message="Knowledge base deleted successfully")
