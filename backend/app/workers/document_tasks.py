"""文档异步处理任务（设计文档 8.1 文档处理流程 + 状态机）。

process_document(document_id)：
1. status -> processing；
2. 读取 storage_path 文件文本（txt/md 直读；pdf 尝试 pypdf，缺失则优雅跳过）；
3. 按 Document.chunk_strategy 切分；
4. 批量 embedding；
5. 写入 DocumentChunk（content/char_count/token_count/embedding/knowledge_base_id/owner_id），
   并用 to_tsvector('simple', content) 回填 tsv 全文列；
6. 成功 -> status=ready + chunk_count；失败 -> status=failed + error_message。

任务自管理 AsyncSession（app.core.db.async_session），不依赖请求上下文。
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.core.db import async_session
from app.models import Document, DocumentChunk
from app.services import chunking
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService


def _read_text_file(path: str, file_type: str) -> str:
    """读取文档文本：txt/md 直读；pdf 走 pypdf；docx 走 python-docx。"""
    ftype = (file_type or "").lower().lstrip(".")
    if ftype == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(path)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            return ""
    if ftype in ("docx", "doc"):
        try:
            from docx import Document as DocxDocument

            doc = DocxDocument(path)
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            return ""
    # txt / md / 其他纯文本
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


async def process_document(document_id: uuid.UUID | str) -> None:
    """文档处理主流程（状态机驱动）。失败时落 failed + error_message。"""
    if isinstance(document_id, str):
        document_id = uuid.UUID(document_id)

    embedding_service = EmbeddingService()
    llm = LLMService()

    async with async_session() as session:
        document = await session.get(Document, document_id)
        if document is None:
            return

        try:
            # 8.1.2 processing
            document.processing_status = "processing"
            session.add(document)
            await session.commit()

            if not document.storage_path:
                raise ValueError("storage_path is empty")

            # 8.1.3 抽取文本 + 切分
            raw_text = _read_text_file(document.storage_path, document.file_type)
            # Unicode NFKC 规范化：将康熙部首字符等异体转为标准汉字，确保检索匹配
            import unicodedata
            raw_text = unicodedata.normalize("NFKC", raw_text)
            pieces = chunking.dispatch(document.chunk_strategy, raw_text)
            if not pieces:
                raise ValueError("no extractable text / chunks produced")

            # 8.1.4 批量向量化
            embeddings = await embedding_service.embed_texts(pieces)

            # 8.1.4 构建 chunk 行
            chunk_objs: list[DocumentChunk] = []
            for idx, (content, vector) in enumerate(zip(pieces, embeddings)):
                chunk_objs.append(
                    DocumentChunk(
                        chunk_index=idx,
                        content=content,
                        char_count=len(content),
                        token_count=llm.count_tokens(content),
                        embedding=vector,
                        document_id=document.id,
                        knowledge_base_id=document.knowledge_base_id,
                        owner_id=document.owner_id,
                    )
                )
            session.add_all(chunk_objs)
            await session.commit()

            # 8.1.4 回填 tsv 全文检索列（用 SQL to_tsvector，简单分词配置）
            await session.exec(
                text(
                    "UPDATE documentchunk "
                    "SET tsv = to_tsvector('simple', content) "
                    "WHERE document_id = :doc_id"
                ),
                params={"doc_id": str(document.id)},
            )

            # 8.1.5 ready
            document.processing_status = "ready"
            document.chunk_count = len(chunk_objs)
            document.error_message = None
            session.add(document)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            # 8.1.5 failed：回滚后单独写状态，保证错误可观测
            await session.rollback()
            document = await session.get(Document, document_id)
            if document is not None:
                document.processing_status = "failed"
                document.error_message = str(exc)[:2000]
                session.add(document)
                await session.commit()
