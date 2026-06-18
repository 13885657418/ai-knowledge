"""重排序服务（设计文档 8.2 步骤 4）。

混合检索（向量 + BM25 + RRF 融合）得到候选集后，用 cross-encoder
对 (query, chunk) 逐对打分重排，提升 top-k 精度。

鲁棒性约束：sentence-transformers / torch 属重依赖，可能未安装或加载失败。
本模块以 try/except 包裹导入与加载，任何异常都优雅降级为「保持融合顺序」，
保证在纯 mock 环境（无 GPU、无模型权重）下也能跑通主链路。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings


class Reranker:
    """Cross-encoder 重排，加载失败时降级为恒等重排（保留 RRF 顺序）。"""

    def __init__(
        self, enabled: bool | None = None, model_name: str | None = None
    ) -> None:
        self.enabled = (
            settings.RERANK_ENABLED if enabled is None else enabled
        )
        self.model_name = model_name or settings.RERANK_MODEL
        self._model: Any = None
        self._load_failed = False

    def _ensure_model(self) -> bool:
        """懒加载 cross-encoder；不可用时置 _load_failed 并返回 False。"""
        if self._model is not None:
            return True
        if self._load_failed or not self.enabled:
            return False
        try:
            # 重依赖延迟导入：缺失时走降级分支
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
            return True
        except Exception:
            # 模型/库不可用 -> 永久降级，不再重试
            self._load_failed = True
            self._model = None
            return False

    async def rerank(
        self, query: str, candidates: list[dict]
    ) -> list[dict]:
        """对候选列表重排，并在每个候选的 scores 中写入 rerank 分。

        candidates: list of dict，至少含 "content" 与 "scores"。
        返回：按 rerank 分降序排列的同结构列表（降级时保持入参顺序）。
        """
        if not candidates:
            return []

        if not self._ensure_model():
            # 降级：保持融合顺序，rerank 分置 None 以示未启用
            for c in candidates:
                c.setdefault("scores", {})["rerank"] = None
            return candidates

        pairs = [[query, c.get("content", "")] for c in candidates]
        try:
            scores = self._model.predict(pairs)
        except Exception:
            # 推理期异常同样降级
            for c in candidates:
                c.setdefault("scores", {})["rerank"] = None
            return candidates

        for c, s in zip(candidates, scores):
            c.setdefault("scores", {})["rerank"] = float(s)
        return sorted(
            candidates, key=lambda c: c["scores"].get("rerank") or 0.0, reverse=True
        )
