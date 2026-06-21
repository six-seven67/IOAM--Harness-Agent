"""认证接口

提供用户注册、登录、Token 刷新、当前用户信息查询四个端点。

响应格式遵循项目统一规范: {code, message, data}
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from loguru import logger

from app.config import config
from app.core.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.core.db import db_manager
from app.core.redis import redis_manager
from app.models.db import User
from app.models.user import (
    UserInfo,
    UserLogin,
    UserRegister,
    UserUpdate,
)

router = APIRouter()


# ═════════════════════════════════════════════════════════════════════════════
# POST /register — 用户注册
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/register")
async def register(form: UserRegister):
    """注册新用户。

    用户名 2-64 字符，密码最少 6 字符。注册成功直接返回 JWT token，
    无需再走登录流程。
    """
    # 获取数据库会话（MySQL 未配置时 get_session 抛 RuntimeError）
    try:
        session_iter = db_manager.get_session()
        session = await anext(session_iter)
    except RuntimeError as e:
        logger.warning(f"注册失败——数据库未配置: {e}")
        return {
            "code": 503,
            "message": "数据库服务未配置，请联系管理员。",
            "data": None,
        }

    try:
        # 检查用户名是否已存在
        result = await session.execute(
            select(User).where(User.username == form.username)
        )
        existing_user = result.scalar_one_or_none()

        if existing_user is not None:
            return {
                "code": 409,
                "message": "用户名已存在，请更换后重试。",
                "data": None,
            }

        # bcrypt 哈希密码
        hashed_pw = hash_password(form.password)

        # 创建用户记录
        user = User(
            username=form.username,
            password=hashed_pw,
            email=form.email or "",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        logger.info(f"新用户注册成功: {user.username} (id={user.id})")

        # 签发 JWT
        token = create_access_token(user.id, user.username)

        # 将 token 存入 Redis（Redis 不可用时静默跳过）
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        ttl = config.jwt_expire_hours * 3600
        await redis_manager.store_token(token_hash, user.id, ttl=ttl)

        return {
            "code": 200,
            "message": "注册成功",
            "data": {
                "access_token": token,
                "token_type": "bearer",
                "user_id": user.id,
                "username": user.username,
            },
        }

    except IntegrityError:
        await session.rollback()
        return {
            "code": 409,
            "message": "用户名已存在，请更换后重试。",
            "data": None,
        }
    except Exception as e:
        await session.rollback()
        logger.error(f"注册异常: {e}")
        return {
            "code": 500,
            "message": f"服务器内部错误: {e}",
            "data": None,
        }
    finally:
        try:
            await session.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# POST /login — 用户登录
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/login")
async def login(form: UserLogin):
    """用户登录。

    验证用户名密码，返回 JWT access token。账号被禁用时返回 403。
    """
    try:
        session_iter = db_manager.get_session()
        session = await anext(session_iter)
    except RuntimeError as e:
        logger.warning(f"登录失败——数据库未配置: {e}")
        return {
            "code": 503,
            "message": "数据库服务未配置，请联系管理员。",
            "data": None,
        }

    try:
        # 查用户
        result = await session.execute(
            select(User).where(User.username == form.username)
        )
        user = result.scalar_one_or_none()

        if user is None:
            return {
                "code": 401,
                "message": "用户名或密码错误。",
                "data": None,
            }

        # 检查账号是否启用
        if not user.is_active:
            logger.warning(f"已禁用账号尝试登录: {user.username}")
            return {
                "code": 403,
                "message": "账号已被禁用，请联系管理员。",
                "data": None,
            }

        # 验证密码
        if not verify_password(form.password, user.password):
            return {
                "code": 401,
                "message": "用户名或密码错误。",
                "data": None,
            }

        logger.info(f"用户登录成功: {user.username} (id={user.id})")

        # 签发 JWT
        token = create_access_token(user.id, user.username)

        # 将 token 存入 Redis
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        ttl = config.jwt_expire_hours * 3600
        await redis_manager.store_token(token_hash, user.id, ttl=ttl)

        return {
            "code": 200,
            "message": "登录成功",
            "data": {
                "access_token": token,
                "token_type": "bearer",
                "user_id": user.id,
                "username": user.username,
            },
        }

    except Exception as e:
        logger.error(f"登录异常: {e}")
        return {
            "code": 500,
            "message": f"服务器内部错误: {e}",
            "data": None,
        }
    finally:
        try:
            await session.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# GET /me — 获取当前用户信息
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    """获取当前登录用户的详细信息。

    需要 Authorization: Bearer <token> 请求头。
    无 token 或 token 无效时返回 401。
    """
    # get_current_user 在无 token 时返回 None（Phase 1 permissive）
    # Phase 2 中，/me 端点要求必须登录
    if current_user is None:
        return {
            "code": 401,
            "message": "请先登录。",
            "data": None,
        }

    try:
        session_iter = db_manager.get_session()
        session = await anext(session_iter)
    except RuntimeError as e:
        logger.warning(f"获取用户信息失败——数据库未配置: {e}")
        return {
            "code": 503,
            "message": "数据库服务未配置。",
            "data": None,
        }

    try:
        result = await session.execute(
            select(User).where(User.id == current_user["user_id"])
        )
        user = result.scalar_one_or_none()

        if user is None or not user.is_active:
            return {
                "code": 401,
                "message": "用户不存在或已禁用。",
                "data": None,
            }

        return {
            "code": 200,
            "message": "success",
            "data": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "avatar": user.avatar,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
        }

    except Exception as e:
        logger.error(f"获取用户信息异常: {e}")
        return {
            "code": 500,
            "message": f"服务器内部错误: {e}",
            "data": None,
        }
    finally:
        try:
            await session.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# PUT /me — 用户信息修改（Phase 6 bugfix）
# ═════════════════════════════════════════════════════════════════════════════

@router.put("/me")
async def update_profile(
    body: UserUpdate,
    current_user: dict = Depends(get_current_user),
):
    """修改当前用户信息：邮箱、头像、密码。

    所有字段可选，只更新非空字段。
    改密码时必须提供旧密码验证。
    """
    if current_user is None:
        return {
            "code": 401,
            "message": "请先登录。",
            "data": None,
        }

    try:
        session_iter = db_manager.get_session()
        session = await anext(session_iter)
    except RuntimeError:
        return {
            "code": 503,
            "message": "数据库未配置，无法修改用户信息。",
            "data": None,
        }

    try:
        # 查询当前用户
        result = await session.execute(
            select(User).where(User.id == current_user["user_id"])
        )
        user = result.scalar_one_or_none()

        if user is None or not user.is_active:
            return {
                "code": 401,
                "message": "用户不存在或已禁用。",
                "data": None,
            }

        updates = {}

        # 修改密码：需要旧密码验证
        if body.new_password:
            if not body.old_password:
                return {
                    "code": 400,
                    "message": "修改密码需要提供旧密码。",
                    "data": None,
                }
            if not verify_password(body.old_password, user.password):
                return {
                    "code": 400,
                    "message": "旧密码不正确。",
                    "data": None,
                }
            updates["password"] = hash_password(body.new_password)

        # 修改邮箱
        if body.email is not None:
            updates["email"] = body.email

        # 修改头像
        if body.avatar is not None:
            updates["avatar"] = body.avatar

        if updates:
            from sqlalchemy import update as sql_update
            await session.execute(
                sql_update(User)
                .where(User.id == user.id)
                .values(**updates)
            )
            await session.commit()
            logger.info(
                f"用户 {user.username} (id={user.id}) 已更新: "
                f"{list(updates.keys())}"
            )

        # 返回更新后的用户信息
        return {
            "code": 200,
            "message": "用户信息已更新。",
            "data": {
                "id": user.id,
                "username": user.username,
                "email": updates.get("email", user.email),
                "avatar": updates.get("avatar", user.avatar),
                "role": user.role,
                "is_active": user.is_active,
                "created_at": (
                    user.created_at.isoformat() if user.created_at else None
                ),
            },
        }

    except Exception as e:
        await session.rollback()
        logger.error(f"更新用户信息异常: {e}")
        return {
            "code": 500,
            "message": f"服务器内部错误: {e}",
            "data": None,
        }
    finally:
        try:
            await session.close()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# POST /refresh — 刷新 Token
# ═════════════════════════════════════════════════════════════════════════════

@router.post("/refresh")
async def refresh(current_user: dict = Depends(get_current_user)):
    """刷新 JWT token。

    使用当前有效 token 换取新 token。旧 token 在过期前仍可使用。
    """
    if current_user is None:
        return {
            "code": 401,
            "message": "请先登录。",
            "data": None,
        }

    try:
        session_iter = db_manager.get_session()
        session = await anext(session_iter)
    except RuntimeError:
        # 数据库不可用时仍然可以签发新 token（JWT 是自包含的）
        new_token = create_access_token(
            current_user["user_id"], current_user["username"]
        )
        return {
            "code": 200,
            "message": "Token 已刷新（注意：数据库未配置，无法验证用户状态）。",
            "data": {
                "access_token": new_token,
                "token_type": "bearer",
                "user_id": current_user["user_id"],
                "username": current_user["username"],
            },
        }

    try:
        result = await session.execute(
            select(User).where(User.id == current_user["user_id"])
        )
        user = result.scalar_one_or_none()

        if user is None or not user.is_active:
            return {
                "code": 401,
                "message": "用户不存在或已禁用。",
                "data": None,
            }

        new_token = create_access_token(user.id, user.username)
        logger.info(f"Token 已刷新: {user.username} (id={user.id})")

        return {
            "code": 200,
            "message": "Token 已刷新。",
            "data": {
                "access_token": new_token,
                "token_type": "bearer",
                "user_id": user.id,
                "username": user.username,
            },
        }

    except Exception as e:
        logger.error(f"刷新 Token 异常: {e}")
        return {
            "code": 500,
            "message": f"服务器内部错误: {e}",
            "data": None,
        }
    finally:
        try:
            await session.close()
        except Exception:
            pass
