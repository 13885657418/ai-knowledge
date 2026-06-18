"""可观测封装（设计文档 4.7 / 11.3）。

封装 Langfuse trace，作为 RAG 主链路的埋点入口。设计目标：
- LANGFUSE_ENABLED=False 或 langfuse 库缺失/初始化失败时，全部降级为 no-op；
- 任何埋点异常都不得影响主链路（best-effort），并始终返回一个 trace_id
  （Langfuse 不可用时用 uuid 兜底）。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.core.config import settings


class _Trace:
    """trace 句柄：持有 trace_id，并对底层 langfuse trace 做容错代理。"""

    def __init__(self, trace_id: str, handle: Any = None) -> None:
        self.trace_id = trace_id
        self._handle = handle

    def update(self, **kwargs: Any) -> None:
        """更新 trace（输出、元数据等）；异常静默吞掉。"""
        if self._handle is None:
            return
        try:
            self._handle.update(**kwargs)
        except Exception:
            pass

    def event(self, name: str, **kwargs: Any) -> None:
        """记录一个阶段事件（检索、生成等）。"""
        if self._handle is None:
            return
        try:
            self._handle.event(name=name, **kwargs)
        except Exception:
            pass


class Observability:
    """Langfuse 客户端封装，不可用时为 no-op。"""

    def __init__(self) -> None:
        self.enabled = bool(settings.LANGFUSE_ENABLED)
        self._client: Any = None
        if self.enabled:
            self._client = self._init_client()
            if self._client is None:
                # 初始化失败 -> 退回 no-op
                self.enabled = False

    def _init_client(self) -> Any:
        try:
            from langfuse import Langfuse

            return Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_HOST,
            )
        except Exception:
            return None

    @asynccontextmanager
    async def trace(
        self, name: str, **kwargs: Any
    ) -> AsyncIterator[_Trace]:
        """异步上下文管理器：进入返回 _Trace，退出 flush（best-effort）。"""
        trace_id = str(uuid.uuid4())
        handle = None
        if self.enabled and self._client is not None:
            try:
                handle = self._client.trace(id=trace_id, name=name, **kwargs)
            except Exception:
                handle = None
        tracer = _Trace(trace_id=trace_id, handle=handle)
        try:
            yield tracer
        finally:
            if self.enabled and self._client is not None:
                try:
                    self._client.flush()
                except Exception:
                    pass
