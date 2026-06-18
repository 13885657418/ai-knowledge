from fastapi import APIRouter

from app.api.routes import (
    Document,
    chat,
    documentchunk,
    eval,
    items,
    knowledge_bases,
    login,
    private,
    prompts,
    tools,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(Document.router)
api_router.include_router(documentchunk.router)
# --- AI 应用路由（设计文档 v2：问答 / Prompt / Agent 工具 / RAG 评估）---
api_router.include_router(chat.router)
api_router.include_router(prompts.router)
api_router.include_router(tools.router)
api_router.include_router(eval.router)

if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
