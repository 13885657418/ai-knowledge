"""DocumentMetaTool：查询文档元数据（设计文档 4.8 本地工具）。

让 Agent 获取某文档的处理状态、chunk 数量、文件名等，
例如回答"这份文档处理好了吗""它被切成了多少段"。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.services.tools.base import BaseTool


class DocumentMetaTool(BaseTool):
    name = "get_document_metadata"
    description = (
        "查询指定文档的元数据：处理状态（pending/processing/ready/failed）、"
        "切分得到的 chunk 数量、原始文件名与文件类型。"
        "当用户询问某文档是否就绪、规模或处理进度时使用。"
    )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "文档 ID（UUID 字符串）",
                }
            },
            "required": ["document_id"],
        }

    async def run(self, args: dict[str, Any], session: Any | None = None) -> Any:
        document_id = args.get("document_id")
        if not document_id:
            return {"error": "缺少必填参数 document_id"}
        if session is None:
            return {
                "document_id": document_id,
                "file_name": "(mock) demo.md",
                "processing_status": "ready",
                "chunk_count": 0,
                "note": "session 不可用，返回 mock 数据",
            }
        try:
            doc_uuid = uuid.UUID(str(document_id))
        except (ValueError, AttributeError):
            return {"error": f"非法的 document_id: {document_id!r}"}

        from app.models import Document

        doc = await session.get(Document, doc_uuid)
        if doc is None:
            return {"error": "文档不存在", "document_id": str(document_id)}
        # 注意：模型字段为 file_name / processing_status / chunk_count（见 app/models.py）
        return {
            "document_id": str(doc.id),
            "file_name": doc.file_name,
            "file_type": doc.file_type,
            "processing_status": doc.processing_status,
            "chunk_count": doc.chunk_count,
            "error_message": doc.error_message,
        }
