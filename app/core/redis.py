"""Redis 客户端管理器

提供延迟连接的 RedisManager 单例，封装 token 存储、限流、查询缓存、Embedding 缓存。
当 config.redis_url 为空时，所有方法返回 None/False，优雅降级。

使用方式（Phase 2+）:
    from app.core.redis import redis_manager

    user_id = await redis_manager.validate_token(token_hash)
    ok = await redis_manager.check_rate_limit(user_id, "/api/chat")
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from loguru import logger

from app.config import config


class RedisManager:
    """Redis 异步客户端管理器（模块级单例）

    关键设计：
    - Client 不在 import 时创建，首次调用 _get_client() 时才连接
    - 连接失败自动降级：_enabled → False，后续调用全部是 no-op
    - 所有业务方法返回 None/False/True (permissive) 而非抛异常
    """

    def __init__(self) -> None:
        self._redis_url: str = config.redis_url
        self._client = None  # type: Optional[redis.asyncio.Redis]
        self._enabled: bool = bool(self._redis_url)

    # ── 客户端延迟连接 ───────────────────────────────────────────────────

    async def _get_client(self):
        """延迟连接并返回 Redis 客户端。未配置或连接失败返回 None。"""
        if not self._enabled:
            return None
        if self._client is None:
            try:
                import redis.asyncio as aioredis

                logger.info(f"连接 Redis -> {self._redis_url}")
                self._client = aioredis.from_url(
                    self._redis_url,
                    decode_responses=True,  # 自动解码 bytes → str
                )
                await self._client.ping()
                logger.info("Redis 连接成功")
            except Exception as e:
                logger.warning(f"Redis 连接失败: {e}，Redis 功能已禁用")
                self._enabled = False
                self._client = None
                return None
        return self._client

    # ── 关闭连接 ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        """关闭 Redis 连接。"""
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("Redis 连接已关闭")

    # ── Token 管理 ───────────────────────────────────────────────────────

    async def store_token(self, token_hash: str, user_id: int, ttl: int = 86400) -> bool:
        """存储 token_hash → user_id 映射，带 TTL（默认 24h）。"""
        client = await self._get_client()
        if client is None:
            return False
        await client.setex(f"auth:token:{token_hash}", ttl, str(user_id))
        return True

    async def validate_token(self, token_hash: str) -> Optional[int]:
        """验证 token 是否有效，返回 user_id 或 None。"""
        client = await self._get_client()
        if client is None:
            return None
        uid = await client.get(f"auth:token:{token_hash}")
        return int(uid) if uid else None

    async def revoke_token(self, token_hash: str) -> bool:
        """删除 token（登出）。"""
        client = await self._get_client()
        if client is None:
            return False
        await client.delete(f"auth:token:{token_hash}")
        return True

    # ── 限流保护 ─────────────────────────────────────────────────────────

    async def check_rate_limit(
        self, user_id: int, endpoint: str, max_rpm: int = 60
    ) -> bool:
        """检查请求是否在限流范围内。

        Redis 不可用时返回 True（允许所有请求），避免误拦。
        使用滑动窗口计数，key 自动在 60s 后过期。
        """
        client = await self._get_client()
        if client is None:
            return True  # 无 Redis → 不限流（permissive）
        key = f"ratelimit:{user_id}:{endpoint}"
        current = await client.incr(key)
        if current == 1:
            await client.expire(key, 60)
        return current <= max_rpm

    # ── 语义查询缓存 ─────────────────────────────────────────────────────

    async def get_cached_answer(self, query: str) -> Optional[str]:
        """查询语义缓存（基于 MD5 规范化 key）。命中返回答案，未命中返回 None。"""
        client = await self._get_client()
        if client is None:
            return None
        h = hashlib.md5(query.strip().lower().encode()).hexdigest()
        return await client.get(f"cache:query:{h}")

    async def set_cached_answer(self, query: str, answer: str, ttl: int = 600) -> bool:
        """缓存查询结果（默认 TTL 10 分钟）。"""
        client = await self._get_client()
        if client is None:
            return False
        h = hashlib.md5(query.strip().lower().encode()).hexdigest()
        await client.setex(f"cache:query:{h}", ttl, answer)
        return True

    # ── Embedding 缓存 ────────────────────────────────────────────────────

    async def get_cached_embedding(self, text: str) -> Optional[list]:
        """获取缓存的 Embedding 向量。命中返回 list[float]，未命中返回 None。"""
        client = await self._get_client()
        if client is None:
            return None
        h = hashlib.md5(text.encode()).hexdigest()
        raw = await client.get(f"cache:embed:{h}")
        return json.loads(raw) if raw else None

    async def set_cached_embedding(
        self, text: str, vector: list, ttl: int = 3600
    ) -> bool:
        """缓存 Embedding 向量（默认 TTL 1 小时）。"""
        client = await self._get_client()
        if client is None:
            return False
        h = hashlib.md5(text.encode()).hexdigest()
        await client.setex(f"cache:embed:{h}", ttl, json.dumps(vector))
        return True

    # ── 健康检查 ─────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """检查 Redis 是否连接正常。"""
        client = await self._get_client()
        if client is None:
            return False
        try:
            await client.ping()
            return True
        except Exception as e:
            logger.warning(f"Redis 健康检查失败: {e}")
            return False


# 全局单例
redis_manager = RedisManager()
