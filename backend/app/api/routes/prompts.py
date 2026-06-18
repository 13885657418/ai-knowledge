"""Prompt 管理接口（设计文档 7.4 / 6.8）。

GET /prompts          列出所有 Prompt 配置
POST /prompts         新建 Prompt 配置
POST /prompts/{id}/activate  激活指定配置（同 name 下其他置为非 active）
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    PromptConfig,
    PromptConfigCreate,
    PromptConfigPublic,
    PromptConfigsPublic,
)
from app.services.prompt_service import PromptService

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("/", response_model=PromptConfigsPublic)
async def list_prompts(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    """列出 Prompt 配置（按创建时间倒序）。"""
    count_result = await session.exec(select(func.count()).select_from(PromptConfig))
    count = count_result.one()
    stmt = (
        select(PromptConfig)
        .order_by(PromptConfig.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.exec(stmt)
    data = [PromptConfigPublic.model_validate(p) for p in result.all()]
    return PromptConfigsPublic(data=data, count=count)


@router.post("/", response_model=PromptConfigPublic)
async def create_prompt(
    *, session: SessionDep, current_user: CurrentUser, prompt_in: PromptConfigCreate
) -> Any:
    """新建 Prompt 配置。"""
    service = PromptService(session)
    return await service.create(prompt_in)


@router.put("/{id}", response_model=PromptConfigPublic)
async def update_prompt(
    *, session: SessionDep, current_user: CurrentUser, id: uuid.UUID, prompt_in: PromptConfigCreate
) -> Any:
    """更新指定 Prompt 配置的内容。"""
    target = await session.get(PromptConfig, id)
    if not target:
        raise HTTPException(status_code=404, detail="Prompt config not found")
    update_data = prompt_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(target, k, v)
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


@router.post("/{id}/activate", response_model=PromptConfigPublic)
async def activate_prompt(
    *, session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Any:
    """激活指定 Prompt 配置；同 name 下其余自动置为非 active。"""
    service = PromptService(session)
    activated = await service.activate(id)
    if not activated:
        raise HTTPException(status_code=404, detail="Prompt config not found")
    return activated
