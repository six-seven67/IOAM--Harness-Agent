"""用户相关 Pydantic 模型

Phase 2 (认证系统) 的请求/响应 schema。
遵循现有 app/models/ 中的 Pydantic BaseModel 模式。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ═════════════════════════════════════════════════════════════════════════════
# 请求模型
# ═════════════════════════════════════════════════════════════════════════════

class UserRegister(BaseModel):
    """用户注册请求。

    密码最少 6 字符；用户名 2-64 字符。
    """

    username: str = Field(
        ..., description="用户名", min_length=2, max_length=64
    )
    password: str = Field(
        ..., description="密码（最少 6 字符）", min_length=6, max_length=128
    )
    email: Optional[str] = Field(
        None, description="邮箱地址", max_length=128
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "operator1",
                "password": "secure123",
                "email": "operator1@example.com",
            }
        }
    }


class UserLogin(BaseModel):
    """用户登录请求。"""

    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")

    model_config = {
        "json_schema_extra": {
            "example": {
                "username": "operator1",
                "password": "secure123",
            }
        }
    }


# ═════════════════════════════════════════════════════════════════════════════
# 响应模型
# ═════════════════════════════════════════════════════════════════════════════

class TokenResponse(BaseModel):
    """登录成功后的 Token 响应。"""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token 类型")
    user_id: int = Field(..., description="用户 ID")
    username: str = Field(..., description="用户名")

    model_config = {
        "json_schema_extra": {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIs...",
                "token_type": "bearer",
                "user_id": 1,
                "username": "operator1",
            }
        }
    }


class UserInfo(BaseModel):
    """公开的用户信息（不含密码）。"""

    id: int = Field(..., description="用户 ID")
    username: str = Field(..., description="用户名")
    email: str = Field(default="", description="邮箱")
    avatar: str = Field(default="", description="头像 URL")
    role: str = Field(default="user", description="角色: user / admin")
    is_active: bool = Field(default=True, description="账号是否启用")
    created_at: Optional[datetime] = Field(None, description="注册时间")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 1,
                "username": "operator1",
                "email": "operator1@example.com",
                "avatar": "",
                "role": "user",
                "is_active": True,
                "created_at": "2026-06-20T10:00:00",
            }
        }
    }


# ═════════════════════════════════════════════════════════════════════════════
# 更新请求模型
# ═════════════════════════════════════════════════════════════════════════════

class UserUpdate(BaseModel):
    """用户信息修改请求。所有字段可选，只更新非空字段。

    改密码时需提供旧密码验证。
    """

    email: Optional[str] = Field(
        None, description="新邮箱地址", max_length=128
    )
    avatar: Optional[str] = Field(
        None, description="新头像 URL", max_length=512
    )
    old_password: Optional[str] = Field(
        None, description="旧密码（改密码时必填）"
    )
    new_password: Optional[str] = Field(
        None, description="新密码（最少 6 字符）", min_length=6, max_length=128
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "new-email@example.com",
                "avatar": "https://example.com/avatar.png",
            }
        }
    }
