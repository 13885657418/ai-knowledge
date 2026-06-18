"""LLMRouter：多模型负载均衡与调度（设计文档 18.2）。

在 LLMService 之上构建主动的负载均衡 + 容灾闭环：

  ChatService ──▶ LLMRouter ──┬─ ProviderPool（权重/健康/限速余量）
                              ├─ Strategy（加权轮询 / 最少在途 / 成本路由）
                              ├─ CircuitBreaker（错误率+延迟滑窗 → 熔断+探活）
                              └─ selected Provider ──▶ 流式/非流式调用
                                       └─ 失败 ──▶ fallback 链

实现的 5 个调度策略（设计文档 18.2）：
1. 加权轮询 / 最少在途请求：打散单 key 限速；
2. 成本/能力分级路由：简单 query → 便宜小模型，复杂/Agent → 强模型；
3. 熔断 + 健康检查：错误率/延迟滑窗超阈值 → open，定时探活 half-open → closed；
4. 限速感知：跟踪各 key 的 RPM/TPM 余量，优先分发余量充足者；
5. 流式兼容：入口选定 Provider 后保持，不中途切换，仅建连失败才 fallback。

可运行性：内置 MockProvider，无需任何 API key 即可跑通选路/熔断/限速/fallback。
文件末尾提供 __main__ demo 可直接 `python -m app.services.llm_router` 观察调度决策。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("llm_router")


# ---------------------------------------------------------------------------
# 任务类型与熔断状态
# ---------------------------------------------------------------------------
class TaskType(str, Enum):
    SIMPLE = "simple"  # 简单问答 / query 改写 / 标题生成 → 便宜小模型
    COMPLEX = "complex"  # 复杂推理 / Agent → 强模型


class BreakerState(str, Enum):
    CLOSED = "closed"  # 正常
    OPEN = "open"  # 熔断中，摘除流量
    HALF_OPEN = "half_open"  # 探活中


class Strategy(str, Enum):
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    LEAST_IN_FLIGHT = "least_in_flight"


# ---------------------------------------------------------------------------
# 熔断器：错误率 + 延迟滑动窗口
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """基于滑动窗口的熔断器（设计文档 18.2 点 3）。

    - 窗口内错误率 > error_threshold 或 平均延迟 > latency_threshold_ms → OPEN；
    - OPEN 持续 cooldown_s 后转 HALF_OPEN 放行探测请求；
    - 探测成功 → CLOSED；失败 → 重新 OPEN。
    """

    def __init__(
        self,
        error_threshold: float = 0.5,
        latency_threshold_ms: float = 8000.0,
        window_size: int = 20,
        cooldown_s: float = 30.0,
        min_samples: int = 5,
    ) -> None:
        self.error_threshold = error_threshold
        self.latency_threshold_ms = latency_threshold_ms
        self.window_size = window_size
        self.cooldown_s = cooldown_s
        self.min_samples = min_samples
        self.state = BreakerState.CLOSED
        self._samples: deque[tuple[bool, float]] = deque(maxlen=window_size)
        self._opened_at: float = 0.0

    def allow(self) -> bool:
        """是否放行请求。"""
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self.state = BreakerState.HALF_OPEN
                return True  # 放一个探测请求
            return False
        # HALF_OPEN：放行探测
        return True

    def record(self, success: bool, latency_ms: float) -> None:
        self._samples.append((success, latency_ms))
        if self.state == BreakerState.HALF_OPEN:
            # 探测结果直接决定恢复或重新熔断
            if success:
                self._close()
            else:
                self._open()
            return
        if len(self._samples) >= self.min_samples and self._should_trip():
            self._open()

    def _should_trip(self) -> bool:
        n = len(self._samples)
        errors = sum(1 for ok, _ in self._samples if not ok)
        avg_latency = sum(lat for _, lat in self._samples) / n
        return (errors / n) > self.error_threshold or avg_latency > self.latency_threshold_ms

    def _open(self) -> None:
        self.state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        logger.warning("熔断器 OPEN：错误率/延迟超阈值，暂时摘除该 provider")

    def _close(self) -> None:
        self.state = BreakerState.CLOSED
        self._samples.clear()
        logger.info("熔断器 CLOSED：探活成功，恢复 provider")


# ---------------------------------------------------------------------------
# 限速计数器：RPM / TPM 余量（in-memory；可替换为 Redis）
# ---------------------------------------------------------------------------
class RateLimitCounter:
    """按 key 跟踪每分钟请求数(RPM)与 token 数(TPM)余量（设计文档 18.2 点 4）。

    用 60s 滚动窗口；这里 in-memory 实现，生产可换 Redis 计数（注释标注）。
    """

    def __init__(self, rpm_limit: int = 60, tpm_limit: int = 100_000) -> None:
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self._req_times: deque[float] = deque()
        self._tok_events: deque[tuple[float, int]] = deque()

    def _evict(self, now: float) -> None:
        while self._req_times and now - self._req_times[0] > 60:
            self._req_times.popleft()
        while self._tok_events and now - self._tok_events[0][0] > 60:
            self._tok_events.popleft()

    def remaining(self, now: float | None = None) -> tuple[int, int]:
        now = now if now is not None else time.monotonic()
        self._evict(now)
        used_tokens = sum(t for _, t in self._tok_events)
        return (
            self.rpm_limit - len(self._req_times),
            self.tpm_limit - used_tokens,
        )

    def has_capacity(self, est_tokens: int = 0) -> bool:
        rpm_left, tpm_left = self.remaining()
        return rpm_left > 0 and tpm_left >= est_tokens

    def record(self, tokens: int, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self._req_times.append(now)
        self._tok_events.append((now, tokens))


# ---------------------------------------------------------------------------
# Provider 抽象与 Pool
# ---------------------------------------------------------------------------
@dataclass
class Provider:
    """池中一个 Provider 条目（一个模型 + 一个 key 的组合）。"""

    name: str
    model: str
    weight: int = 1
    tier: TaskType = TaskType.SIMPLE  # 该 provider 适合的任务等级
    cost_per_1k: float = 0.0  # 每千 token 估算成本，用于成本路由度量
    client: Any | None = None  # 实际 LLM 客户端（LLMService / SDK）
    in_flight: int = 0  # 当前在途请求数（最少在途策略用）
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    rate: RateLimitCounter = field(default_factory=RateLimitCounter)

    def healthy(self) -> bool:
        return self.breaker.allow()


class ProviderPool:
    def __init__(self, providers: list[Provider]) -> None:
        if not providers:
            raise ValueError("ProviderPool 至少需要一个 Provider")
        self.providers = providers
        self._rr_cursor = 0  # 加权轮询游标
        # 展开加权列表：weight 越大出现次数越多
        self._weighted: list[int] = []
        for idx, p in enumerate(providers):
            self._weighted.extend([idx] * max(1, p.weight))

    def candidates(self, tier: TaskType | None, est_tokens: int) -> list[Provider]:
        """筛选可用候选：健康 + 有限速余量（+ 匹配任务等级）。"""
        result = []
        for p in self.providers:
            if not p.healthy():
                continue
            if not p.rate.has_capacity(est_tokens):
                continue
            if tier is not None and p.tier != tier:
                continue
            result.append(p)
        return result


# ---------------------------------------------------------------------------
# Mock Provider 客户端：无 key 可跑
# ---------------------------------------------------------------------------
class MockProvider:
    """模拟 LLM 客户端，用于无 key 环境跑通调度链路。

    :param fail: True 时 chat 抛错，用于演示熔断/fallback。
    :param latency_ms: 模拟延迟，用于演示延迟熔断。
    """

    def __init__(self, name: str, fail: bool = False, latency_ms: float = 50.0) -> None:
        self.name = name
        self.fail = fail
        self.latency_ms = latency_ms

    async def chat(self, messages: list[dict[str, Any]], **_: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError(f"[{self.name}] 模拟调用失败")
        return {
            "role": "assistant",
            "content": f"[{self.name}] mock 回复",
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }

    async def stream(self, messages: list[dict[str, Any]], **_: Any):
        if self.fail:
            raise RuntimeError(f"[{self.name}] 模拟流式失败")
        for tok in ["[", self.name, "] ", "mock ", "stream"]:
            yield tok


# ---------------------------------------------------------------------------
# LLMRouter 主体
# ---------------------------------------------------------------------------
class LLMRouter:
    def __init__(
        self,
        pool: ProviderPool | None = None,
        strategy: Strategy = Strategy.LEAST_IN_FLIGHT,
        complex_query_chars: int = 200,
    ) -> None:
        self.pool = pool or self._default_mock_pool()
        self.strategy = strategy
        self.complex_query_chars = complex_query_chars
        self.decisions: list[dict[str, Any]] = []  # 调度决策日志，便于复盘

    @staticmethod
    def _default_mock_pool() -> ProviderPool:
        """无配置时构造一个含强/弱模型的 mock 池。"""
        return ProviderPool(
            [
                Provider(
                    name="mock-cheap",
                    model="gpt-4o-mini",
                    weight=3,
                    tier=TaskType.SIMPLE,
                    cost_per_1k=0.0005,
                    client=MockProvider("mock-cheap"),
                ),
                Provider(
                    name="mock-strong",
                    model="gpt-4o",
                    weight=1,
                    tier=TaskType.COMPLEX,
                    cost_per_1k=0.005,
                    client=MockProvider("mock-strong"),
                ),
            ]
        )

    # ---- 成本/能力分级路由（策略 2）----
    def classify(self, query: str, use_agent: bool = False) -> TaskType:
        """按启发式判定任务等级：Agent 或长 query → COMPLEX，否则 SIMPLE。"""
        if use_agent:
            return TaskType.COMPLEX
        if query and len(query) >= self.complex_query_chars:
            return TaskType.COMPLEX
        return TaskType.SIMPLE

    # ---- 选路：策略 1（轮询/最少在途）+ 策略 4（限速感知）----
    def select(
        self,
        task_type: TaskType | None = None,
        query: str = "",
        use_agent: bool = False,
        est_tokens: int = 0,
    ) -> Provider:
        """选定一个 Provider。

        先按任务等级筛选；若该等级无健康/有余量候选，则放宽到全等级，
        最终仍无候选时退回池中第一个（由调用方 fallback 兜底）。
        """
        tier = task_type or self.classify(query, use_agent)
        candidates = self.pool.candidates(tier, est_tokens)
        if not candidates:
            # 放宽任务等级限制（宁可用强模型也别不可用）
            candidates = self.pool.candidates(None, est_tokens)
        if not candidates:
            logger.warning("无健康/有余量候选，退回首个 provider（依赖 fallback）")
            chosen = self.pool.providers[0]
        else:
            chosen = self._apply_strategy(candidates)
        self._log_decision(chosen, tier, "select")
        return chosen

    def _apply_strategy(self, candidates: list[Provider]) -> Provider:
        if self.strategy == Strategy.LEAST_IN_FLIGHT:
            return min(candidates, key=lambda p: p.in_flight)
        # 加权轮询：在候选集合上按权重游标推进
        names = {p.name for p in candidates}
        n = len(self.pool._weighted)
        for _ in range(n):
            idx = self.pool._weighted[self.pool._rr_cursor % n]
            self.pool._rr_cursor += 1
            p = self.pool.providers[idx]
            if p.name in names:
                return p
        return candidates[0]

    def _log_decision(self, provider: Provider, tier: TaskType, phase: str) -> None:
        entry = {
            "phase": phase,
            "provider": provider.name,
            "model": provider.model,
            "tier": tier.value,
            "breaker": provider.breaker.state.value,
            "in_flight": provider.in_flight,
        }
        self.decisions.append(entry)
        logger.info("LLM 调度决策: %s", entry)

    def _fallback_chain(self, primary: Provider) -> list[Provider]:
        """primary 之后的 fallback 顺序：其余健康 provider 按权重降序。"""
        others = [p for p in self.pool.providers if p.name != primary.name]
        others.sort(key=lambda p: p.weight, reverse=True)
        return [primary, *others]

    # ---- 非流式调用：选路 + 执行 + 熔断记账 + fallback 链 ----
    async def chat(
        self,
        messages: list[dict[str, Any]],
        query: str = "",
        use_agent: bool = False,
        est_tokens: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        primary = self.select(query=query, use_agent=use_agent, est_tokens=est_tokens)
        last_error: Exception | None = None
        for provider in self._fallback_chain(primary):
            if not provider.healthy():
                continue
            provider.in_flight += 1
            start = time.monotonic()
            try:
                client = provider.client or MockProvider(provider.name)
                result = await client.chat(messages, **kwargs)
                latency = (time.monotonic() - start) * 1000
                provider.breaker.record(True, latency)
                usage = result.get("usage", {}) if isinstance(result, dict) else {}
                provider.rate.record(int(usage.get("total_tokens", est_tokens) or 0))
                if isinstance(result, dict):
                    result["_provider"] = provider.name
                    result["_model"] = provider.model
                return result
            except Exception as exc:  # noqa: BLE001 - 失败则记账并 fallback
                latency = (time.monotonic() - start) * 1000
                provider.breaker.record(False, latency)
                last_error = exc
                logger.warning("provider %s 调用失败，fallback：%s", provider.name, exc)
            finally:
                provider.in_flight -= 1
        raise RuntimeError(f"所有 provider 均不可用，最后错误：{last_error}")

    # ---- 流式调用：选定后保持在该 provider，不中途切换（策略 5）----
    async def stream(
        self,
        messages: list[dict[str, Any]],
        query: str = "",
        use_agent: bool = False,
        **kwargs: Any,
    ):
        primary = self.select(query=query, use_agent=use_agent)
        # 流式仅在"建连失败"时 fallback；一旦开始产出 token 即不再切换。
        for provider in self._fallback_chain(primary):
            if not provider.healthy():
                continue
            client = provider.client or MockProvider(provider.name)
            provider.in_flight += 1
            start = time.monotonic()
            try:
                stream_iter = client.stream(messages, **kwargs)
                # 先尝试取第一个 token 以确认建连成功（失败可 fallback）
                first = await stream_iter.__anext__()
            except StopAsyncIteration:
                provider.in_flight -= 1
                provider.breaker.record(True, (time.monotonic() - start) * 1000)
                return
            except Exception as exc:  # noqa: BLE001 - 建连失败才 fallback
                provider.in_flight -= 1
                provider.breaker.record(False, (time.monotonic() - start) * 1000)
                logger.warning("流式建连失败 %s，fallback：%s", provider.name, exc)
                continue
            # 建连成功：锁定该 provider，持续产出（不再切换）
            try:
                yield first
                async for tok in stream_iter:
                    yield tok
                provider.breaker.record(True, (time.monotonic() - start) * 1000)
            finally:
                provider.in_flight -= 1
            return
        raise RuntimeError("所有 provider 流式建连均失败")


# 便于本地调试调度行为：python -m app.services.llm_router
if __name__ == "__main__":  # pragma: no cover
    import asyncio
    import json

    logging.basicConfig(level=logging.INFO)

    async def _demo() -> None:
        router = LLMRouter(strategy=Strategy.WEIGHTED_ROUND_ROBIN)
        # 简单 query → 走便宜模型
        r1 = await router.chat([{"role": "user", "content": "你好"}], query="你好")
        # 复杂/agent → 走强模型
        r2 = await router.chat(
            [{"role": "user", "content": "x" * 300}], query="x" * 300, use_agent=True
        )
        print("简单任务 ->", r1.get("_provider"), r1.get("_model"))  # noqa: T201
        print("复杂任务 ->", r2.get("_provider"), r2.get("_model"))  # noqa: T201

        # 演示熔断 + fallback：把 cheap provider 设为必然失败
        bad_pool = ProviderPool(
            [
                Provider(name="bad", model="m1", weight=1, client=MockProvider("bad", fail=True)),
                Provider(name="good", model="m2", weight=1, client=MockProvider("good")),
            ]
        )
        r3router = LLMRouter(pool=bad_pool)
        r3 = await r3router.chat([{"role": "user", "content": "hi"}])
        print("fallback ->", r3.get("_provider"))  # noqa: T201

        # 流式 demo
        chunks = []
        async for tok in router.stream([{"role": "user", "content": "hi"}]):
            chunks.append(tok)
        print("stream ->", "".join(chunks))  # noqa: T201
        print("decisions:", json.dumps(router.decisions, ensure_ascii=False))  # noqa: T201

    asyncio.run(_demo())
