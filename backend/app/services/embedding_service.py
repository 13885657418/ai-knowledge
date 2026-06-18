"""Embedding 服务（设计文档 4.4 / 8.2 步骤 3、8.1 步骤 4）。

抽象 Embedding Provider，目的是把「向量化能力」与具体供应商解耦：
- 便于本地零依赖跑通（mock：无需任何 API Key）；
- 便于在 openai / 自部署模型间热切换，且上层检索逻辑无感知；
- 维度由 settings.EMBEDDING_DIM 统一控制，与 document_chunks.embedding vector(N) 对齐。

对外暴露统一异步接口：
- embed_texts(list[str]) -> list[list[float]]   批量（文档入库）
- embed_query(str)       -> list[float]          单条（在线检索）
"""

from __future__ import annotations

import hashlib
import math

from app.core.config import settings


class EmbeddingService:
    """Embedding Provider 抽象 + 统一入口。

    provider 由 settings.EMBEDDING_PROVIDER 决定，默认 mock，
    保证在无 API Key 的环境下也能完成 smoke test。
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        dim: int | None = None,
    ) -> None:
        self.provider = (provider or settings.EMBEDDING_PROVIDER or "mock").lower()
        self.model = model or settings.EMBEDDING_MODEL
        self.dim = dim or settings.EMBEDDING_DIM
        self._client = None  # 懒加载 AsyncOpenAI

    # ---------------------------------------------------------------- mock
    def _mock_embed(self, text: str) -> list[float]:
        """确定性伪随机归一化向量：由文本哈希播种。

        同一文本 -> 同一向量（可复现），不同文本向量不同；
        归一化后配合 pgvector 余弦距离即可得到稳定的相对排序。
        """
        vec: list[float] = []
        # 用滚动哈希填满 dim 维，保证确定性与足够的分散度
        counter = 0
        while len(vec) < self.dim:
            h = hashlib.sha256(f"{text}#{counter}".encode("utf-8")).digest()
            for b in h:
                if len(vec) >= self.dim:
                    break
                # 映射到 [-1, 1)
                vec.append((b / 127.5) - 1.0)
            counter += 1
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    # -------------------------------------------------------------- openai
    def _get_openai_client(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
            )
        return self._client

    async def _openai_embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_openai_client()
        resp = await client.embeddings.create(model=self.model, input=texts)
        # OpenAI 按输入顺序返回，保持索引对齐
        return [item.embedding for item in resp.data]

    # ---------------------------------------------------------------- API
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量向量化（文档入库路径）。"""
        if not texts:
            return []
        if self.provider == "openai":
            return await self._openai_embed(texts)
        # 默认 mock
        return [self._mock_embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        """单条查询向量化（在线检索路径）。"""
        result = await self.embed_texts([text])
        return result[0] if result else [0.0] * self.dim
