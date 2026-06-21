"""SQLAlchemy ORM 数据模型

定义 User（用户）、Conversation（会话）、Message（消息）三个核心表。
使用 SQLAlchemy 2.0 DeclarativeBase + Mapped 注解风格。

Phase 1 仅定义模型结构，不建立 ORM 级 relationship。
外键约束只在数据库层面生效（见 scripts/init_db.sql）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""
    pass


# ═════════════════════════════════════════════════════════════════════════════
# User — 用户账户
# ═════════════════════════════════════════════════════════════════════════════

class User(Base):
    """用户账户模型。

    password 字段存储 bcrypt 哈希（60 字符 + 盐值），最长约 256 字符。
    role 用 MySQL ENUM 约束，可选 'user' 或 'admin'。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    password: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="bcrypt hash"
    )
    email: Mapped[str] = mapped_column(
        String(128), default="", server_default=""
    )
    avatar: Mapped[str] = mapped_column(
        String(512), default="", server_default=""
    )
    role: Mapped[str] = mapped_column(
        Enum("user", "admin", name="user_role"),
        default="user",
        server_default="user",
    )
    is_active: Mapped[bool] = mapped_column(
        default=True, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="CURRENT_TIMESTAMP"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default="CURRENT_TIMESTAMP",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username='{self.username}')>"


# ═════════════════════════════════════════════════════════════════════════════
# Conversation — 对话会话
# ═════════════════════════════════════════════════════════════════════════════

class Conversation(Base):
    """会话模型。

    每个用户可以有多个会话，每个会话包含多条消息。
    session_id 是前端生成的唯一标识，与 LangGraph thread_id 一一对应。
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    session_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
        comment="前端生成的会话唯一标识"
    )
    title: Mapped[str] = mapped_column(
        String(256), default="", server_default="",
        comment="自动生成的会话标题"
    )
    model: Mapped[str] = mapped_column(
        String(64), default="qwen-max", server_default="qwen-max"
    )
    message_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default="CURRENT_TIMESTAMP"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default="CURRENT_TIMESTAMP",
    )

    def __repr__(self) -> str:
        return (
            f"<Conversation(id={self.id}, session_id='{self.session_id}', "
            f"user_id={self.user_id})>"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Message — 聊天消息（完整持久化）
# ═════════════════════════════════════════════════════════════════════════════

class Message(Base):
    """消息模型。

    完整记录每条消息：用户提问、AI 回答、系统提示、工具调用结果。
    tool_calls 字段存储 JSON，格式为 [{"name": ..., "args": ..., "result": ...}]。
    注意：metadata 字段在数据库中命名为 metadata_，避免与 SQLAlchemy MetaData 冲突。
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(
        Enum("user", "assistant", "system", "tool", name="message_role"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    tool_calls: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="工具调用记录 [{name, args, result}]"
    )
    token_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", comment="本消息的 token 数"
    )
    extra_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata_", JSON, default=None, comment="扩展元数据"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=datetime.utcnow,
        server_default="CURRENT_TIMESTAMP(3)",
    )

    def __repr__(self) -> str:
        return (
            f"<Message(id={self.id}, role='{self.role}', "
            f"conversation_id={self.conversation_id})>"
        )
