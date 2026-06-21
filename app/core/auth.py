"""认证工具模块

提供:
- JWT access token 签发与验证（HS256）
- bcrypt 密码哈希与校验
- get_current_user: FastAPI Depends() 依赖，从 Bearer token 提取当前用户

使用方式（Phase 2+）:
    from app.core.auth import get_current_user, create_access_token, hash_password

    @router.post("/login")
    async def login(form: UserLogin):
        # ... 验证密码 ...
        token = create_access_token(user.id, user.username)
        return TokenResponse(access_token=token, ...)

    @router.get("/me")
    async def me(user: dict = Depends(get_current_user)):
        return {"user_id": user["user_id"]}
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

from app.config import config

# 模块级单例：HTTPBearer 安全方案（auto_error=False 允许无 token 请求通过）
_bearer_scheme = HTTPBearer(auto_error=False)


# ═════════════════════════════════════════════════════════════════════════════
# JWT 工具
# ═════════════════════════════════════════════════════════════════════════════

def create_access_token(user_id: int, username: str) -> str:
    """签发 JWT access token。

    Args:
        user_id: 用户数据库主键
        username: 登录用户名

    Returns:
        编码后的 JWT 字符串，payload 含 sub, username, iat, exp
    """
    now = datetime.now(timezone.utc)
    payload: Dict[str, Any] = {
        "sub": str(user_id),       # JWT 标准："subject"，存用户 ID
        "username": username,      # 方便日志/调试，避免每次查库
        "iat": now,                # 签发时间
        "exp": now + timedelta(hours=config.jwt_expire_hours),  # 过期时间
    }
    token = jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)
    return token


def verify_token(token: str) -> Dict[str, Any]:
    """验证并解码 JWT token。

    Returns:
        解码后的 payload 字典

    Raises:
        jwt.ExpiredSignatureError: token 已过期
        jwt.InvalidTokenError: token 无效（签名错误、格式错误等）
    """
    return jwt.decode(
        token,
        config.jwt_secret,
        algorithms=[config.jwt_algorithm],
    )


# ═════════════════════════════════════════════════════════════════════════════
# 密码工具
# ═════════════════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    """对明文密码进行 bcrypt 哈希。

    bcrypt 自动生成随机盐值并嵌入结果字符串中，无需单独管理盐值。

    Args:
        password: 明文密码

    Returns:
        bcrypt 哈希字符串（含算法标识 + 盐值 + 哈希值）
    """
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=config.bcrypt_rounds)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """校验明文密码是否匹配 bcrypt 哈希。

    Args:
        password: 用户输入的明文密码
        hashed: 数据库中存储的 bcrypt 哈希

    Returns:
        True 表示密码正确
    """
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ═════════════════════════════════════════════════════════════════════════════
# FastAPI 依赖：获取当前用户
# ═════════════════════════════════════════════════════════════════════════════

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[Dict[str, Any]]:
    """FastAPI 依赖：从 Authorization: Bearer <token> 头提取当前用户。

    Phase 1 行为:
        - 无 token → 返回 None（向后兼容，现有端点不加鉴权也能运行）
        - token 有效 → 返回 {"user_id": int, "username": str}
        - token 无效/过期 → 抛出 401

    Phase 2 将增强为查数据库验证用户是否存在。

    用法:
        @router.get("/protected")
        async def protected(user: dict = Depends(get_current_user)):
            if user is None:
                raise HTTPException(401)
            ...
    """
    # 没有 Authorization 头 → 返回 None（Phase 1: permissive）
    if credentials is None:
        return None

    token = credentials.credentials

    try:
        payload = verify_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(payload["sub"])
    username = payload["username"]

    logger.debug(f"已认证用户: {username} (id={user_id})")

    return {"user_id": user_id, "username": username}
