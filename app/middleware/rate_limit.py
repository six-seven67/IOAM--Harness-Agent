"""速率限制 ASGI 中间件

Phase 5: 将 Redis 限流能力接入 FastAPI 请求管道。

核心设计：
- 纯 ASGI middleware，在请求到达路由之前拦截
- 从 Authorization header 提取 Bearer token → 解析 user_id
- 调用 redis_manager.check_rate_limit()（P2 已实现滑动窗口计数）
- Redis 不可用时自动放行（permissive 降级，不影响核心功能）
- 无 token 的请求使用客户端 IP 作为标识（匿名限流）

使用方式（main.py）:
    from app.core.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
"""

from __future__ import annotations

import hashlib

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from loguru import logger

from app.core.auth import verify_token
from app.core.redis import redis_manager


# 免限流路径（认证端点 + 静态资源 + 健康检查）
_EXEMPT_PATHS = {
    "/api/auth/register",
    "/api/auth/login",
    "/api/health",
    "/docs",
    "/openapi.json",
}

# 匿名用户每分钟最大请求数
_ANON_MAX_RPM = 20


class RateLimitMiddleware(BaseHTTPMiddleware):
    """纯 ASGI 速率限制中间件。

    每个请求在到达路由处理器之前经过 dispatch()。
    限流按 (user_id, endpoint) 维度计数。
    """

    async def dispatch(self, request: Request, call_next):
        # ① 免限流路径直接放行
        path = request.url.path.rstrip("/")
        if path in _EXEMPT_PATHS or path.startswith("/static"):
            return await call_next(request)

        # ② 解析用户身份
        user_id = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                payload = verify_token(token)
                user_id = payload["sub"]  # string: user_id
            except Exception:
                pass  # token 无效 → 降级为匿名限流

        # ③ 确定限流 key
        if user_id is not None:
            rate_key = f"user:{user_id}"
        else:
            # 匿名用户按 IP 限流
            client_ip = request.client.host if request.client else "unknown"
            ip_hash = hashlib.md5(client_ip.encode()).hexdigest()[:8]
            rate_key = f"anon:{ip_hash}"

        # ④ 调用 Redis 限流
        max_rpm = _ANON_MAX_RPM if user_id is None else 60
        allowed = await redis_manager.check_rate_limit(
            user_id=0 if user_id is None else int(user_id),
            endpoint=rate_key,  # 用合成 key 区分匿名/登录用户
            max_rpm=max_rpm,
        )

        if not allowed:
            logger.warning(f"速率限制触发: key={rate_key}, path={path}")
            return JSONResponse(
                status_code=429,
                content={
                    "code": 429,
                    "message": "请求过于频繁，请稍后再试。",
                    "data": None,
                },
                headers={"Retry-After": "60"},
            )

        # ⑤ 放行
        return await call_next(request)
