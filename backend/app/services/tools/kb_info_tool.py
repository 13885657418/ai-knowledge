"""KbInfoTool：查询知识库基础信息（设计文档 4.8 ToolRegistry 本地工具）。

让 Agent 能在推理过程中获取知识库名称、文档数量等元信息，
例如回答"这个知识库里有多少文档"或先确认知识库是否存在再检索。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.services.tools.base import BaseTool


class KbInfoTool(BaseTool):
    name = "get_knowledge_base_info"
    description = (
        "查询指定知识库的基础信息，包括名称、描述与已入库文档数量。"
        "当用户询问知识库概况、文档规模，或需要在检索前确认知识库存在时使用。"
    )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kb_id": {
                    "type": "string",
                    "description": "知识库 ID（UUID 字符串）",
                }
            },
            "required": ["kb_id"],
        }

    async def run(self, args: dict[str, Any], session: Any | None = None) -> Any:
        kb_id = args.get("kb_id")
        if not kb_id:
            return {"error": "缺少必填参数 kb_id"}
        if session is None:
            # mock 路径：无数据库时返回可解释的占位结果，保证 Agent 链路可跑
            return {
                "kb_id": kb_id,
                "name": "(mock) demo knowledge base",
                "document_count": 0,
                "note": "session 不可用，返回 mock 数据",
            }
        try:
            kb_uuid = uuid.UUID(str(kb_id))
        except (ValueError, AttributeError):
            return {"error": f"非法的 kb_id: {kb_id!r}"}

        # 延迟导入，避免在缺少 DB 模型环境时影响 py_compile
        from sqlalchemy import func, select

        from app.models import Document, KnowledgeBase

        kb = await session.get(KnowledgeBase, kb_uuid)
        if kb is None:
            return {"error": "知识库不存在", "kb_id": str(kb_id)}

        count_stmt = (
            select(func.count())
            .select_from(Document)
            .where(Document.knowledge_base_id == kb_uuid)
        )
        result = await session.execute(count_stmt)
        doc_count = int(result.scalar_one())
        return {
            "kb_id": str(kb.id),
            "name": kb.name,
            "description": kb.description,
            "document_count": doc_count,
        }
