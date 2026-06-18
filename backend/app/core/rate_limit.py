"""按用户限流（设计文档 4.1 / 11.2）。

使用 Redis 的固定窗口计数器实现「每分钟最多 N 次」LLM 调用限流，
保护大模型调用成本。Redis 不可用时降级为「放行」，不阻断主流程
（本地演示友好，生产可改为 fail-closed）。
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis

from app.core.config import settings

# 全局异步 Redis 连接池（按需懒加载）
_redis_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """获取共享的异步 Redis 客户端。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL, encoding="utf-8", decode_responses=True
        )
    return _redis_client


async def check_rate_limit(
    user_id: str, limit: int | None = None, window_seconds: int = 60
) -> tuple[bool, int]:
    """固定窗口限流判定。

    Returns:
        (allowed, remaining)：是否放行 + 当前窗口剩余次数。
    """
    limit = limit or settings.RATE_LIMIT_PER_MINUTE
    # 以 user_id + 当前窗口编号为 key，窗口滚动自动失效
    window = int(time.time()) // window_seconds
    key = f"ratelimit:{user_id}:{window}"
    try:
        client = get_redis()
        current = await client.incr(key)
        if current == 1:
            # 第一次写入时设置过期，保证 key 自动清理
            await client.expire(key, window_seconds)
        allowed = current <= limit
        remaining = max(0, limit - current)
        return allowed, remaining
    except Exception:
        # Redis 故障时降级放行，避免限流组件拖垮主链路
        return True, limit
