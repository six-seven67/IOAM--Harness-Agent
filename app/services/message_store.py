"""MySQL 消息持久化存储

实现四层上下文管理的 Layer 1（MySQL 完整存储）。

核心职责：
1. 异步写入用户消息和 AI 回答到 MySQL（不阻塞对话流）
2. 自动创建/更新 Conversation 记录
3. 从 MySQL 加载历史消息（用于恢复对话上下文）
4. 写入失败只记日志，不影响对话功能（优雅降级）

设计原则：
- 异步写入：消息落盘在后台完成，聊天响应不受影响
- 优雅降级：DB 不可用时跳过持久化，系统照常运行
- 职责分离：MessageStore 只管消息存储，不涉及 LangGraph state

使用方式：
    from app.services.message_store import message_store

    cid = await message_store.ensure_conversation(user_id, session_id, title)
    await message_store.save_message(user_id, cid, "user", question)
    await message_store.save_message(user_id, cid, "assistant", answer)
    history = await message_store.load_history(user_id, session_id)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger
from sqlalchemy import desc, select, text, update

from app.config import config
from app.core.db import db_manager
from app.models.db import Conversation, Message


class MessageStore:
    """消息持久化存储。

    封装 MySQL 中 conversations 和 messages 两个表的 CRUD 操作。
    所有方法都是异步的，写入操作为 fire-and-forget 模式。
    """

    # ── Conversation 管理 ────────────────────────────────────────────────

    async def ensure_conversation(
        self,
        user_id: int,
        session_id: str,
        title: str = "",
        model: str = "",
    ) -> int:
        """确保会话记录存在，不存在则创建。

        Args:
            user_id: 用户 ID
            session_id: 会话标识（对应 LangGraph thread_id）
            title: 会话标题（默认取用户第一条消息前 80 字符）
            model: 使用的模型名

        Returns:
            int: conversation 主键 ID

        当 DB 不可用时返回 -1，调用方应检查并跳过后续写入。
        """
        if not db_manager._enabled:
            return -1

        try:
            session_iter = db_manager.get_session()
            session = await anext(session_iter)
        except RuntimeError:
            logger.debug("MySQL 未配置，跳过会话创建")
            return -1

        try:
            # 先查是否存在
            result = await session.execute(
                select(Conversation).where(Conversation.session_id == session_id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # 更新 updated_at
                await session.execute(
                    update(Conversation)
                    .where(Conversation.id == existing.id)
                    .values(updated_at=datetime.utcnow())
                )
                await session.commit()
                return existing.id

            # 不存在则创建
            conv = Conversation(
                user_id=user_id,
                session_id=session_id,
                title=title[:256] if title else "",
                model=model or config.dashscope_model,
                message_count=0,
            )
            session.add(conv)
            await session.commit()
            await session.refresh(conv)

            logger.info(f"创建会话: id={conv.id}, session_id={session_id}")
            return conv.id

        except Exception as e:
            await session.rollback()
            logger.warning(f"创建会话失败，跳过持久化: {e}")
            return -1
        finally:
            try:
                await session.close()
            except Exception:
                pass

    # ── 消息保存 ─────────────────────────────────────────────────────────

    async def save_message(
        self,
        user_id: int,
        conversation_id: int,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        token_count: int = 0,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """保存一条消息到 MySQL。

        Args:
            user_id: 用户 ID
            conversation_id: 会话主键（来自 ensure_conversation）
            role: 角色（user / assistant / system / tool）
            content: 消息文本内容
            tool_calls: 工具调用记录 [{name, args, result}]
            token_count: 本消息的 token 数（0 表示未计算）
            extra_metadata: 扩展元数据（如模型名、延迟等）

        Returns:
            bool: 是否写入成功

        写入失败不抛异常，只返回 False 并记日志。
        """
        if conversation_id < 0:
            return False  # conversation 创建已失败，跳过

        if not db_manager._enabled:
            return False

        try:
            session_iter = db_manager.get_session()
            session = await anext(session_iter)
        except RuntimeError:
            return False

        try:
            msg = Message(
                conversation_id=conversation_id,
                user_id=user_id,
                role=role,
                content=content,
                tool_calls=tool_calls if tool_calls else None,
                token_count=token_count,
                extra_metadata=extra_metadata,
            )
            session.add(msg)
            await session.commit()

            # 递增 conversation 的消息计数
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(
                    message_count=Conversation.message_count + 1,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()

            logger.debug(f"消息已保存: [{role}] {content[:50]}...")
            return True

        except Exception as e:
            await session.rollback()
            logger.warning(f"消息保存失败: {e}")
            return False
        finally:
            try:
                await session.close()
            except Exception:
                pass

    def save_message_fire_and_forget(
        self,
        user_id: int,
        conversation_id: int,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        token_count: int = 0,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """fire-and-forget 模式写入（不等待结果，不阻塞对话流）。

        使用 asyncio.ensure_future 在后台执行，写入失败不影响对话。
        注意：这是同步方法，必须在运行中的 event loop 内调用。
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.save_message(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                    token_count=token_count,
                    extra_metadata=extra_metadata,
                )
            )
        except RuntimeError:
            # 没有运行中的 event loop，跳过（例如在同步脚本中）
            logger.debug("无运行中的 event loop，跳过 fire-and-forget 消息写入")

    # ── 历史加载 ─────────────────────────────────────────────────────────

    async def load_history(
        self,
        user_id: int,
        session_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """从 MySQL 加载指定会话的历史消息。

        Args:
            user_id: 用户 ID
            session_id: 会话标识
            limit: 最大返回条数（默认 50）

        Returns:
            list[dict]: 消息列表（时间顺序），格式为：
                [{"role": "user", "content": "...", "timestamp": "..."}, ...]

        当 DB 不可用时返回空列表。
        """
        if not db_manager._enabled:
            return []

        try:
            session_iter = db_manager.get_session()
            session = await anext(session_iter)
        except RuntimeError:
            return []

        try:
            # 先通过 session_id 找到 conversation
            result = await session.execute(
                select(Conversation).where(Conversation.session_id == session_id)
            )
            conv = result.scalar_one_or_none()
            if not conv:
                return []

            # 加载消息（时间升序）
            result = await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conv.id,
                    Message.user_id == user_id,
                )
                .order_by(Message.created_at.asc())
                .limit(limit)
            )
            messages = result.scalars().all()

            history: List[Dict[str, Any]] = []
            for msg in messages:
                history.append({
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": msg.tool_calls,
                    "token_count": msg.token_count,
                    "timestamp": (
                        msg.created_at.isoformat()
                        if msg.created_at else ""
                    ),
                })

            logger.debug(
                f"加载会话历史: session_id={session_id}, "
                f"消息数={len(history)}"
            )
            return history

        except Exception as e:
            logger.warning(f"加载历史消息失败: {e}")
            return []
        finally:
            try:
                await session.close()
            except Exception:
                pass

    async def load_user_conversations(
        self,
        user_id: int,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """获取用户的最近会话列表。

        Args:
            user_id: 用户 ID
            limit: 最大返回条数

        Returns:
            list[dict]: 会话列表（时间倒序），格式为：
                [{"session_id": "...", "title": "...", "message_count": N, ...}, ...]
        """
        if not db_manager._enabled:
            return []

        try:
            session_iter = db_manager.get_session()
            session = await anext(session_iter)
        except RuntimeError:
            return []

        try:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(desc(Conversation.updated_at))
                .limit(limit)
            )
            convs = result.scalars().all()

            return [
                {
                    "id": c.id,
                    "session_id": c.session_id,
                    "title": c.title or "（无标题）",
                    "model": c.model,
                    "message_count": c.message_count,
                    "created_at": c.created_at.isoformat() if c.created_at else "",
                    "updated_at": c.updated_at.isoformat() if c.updated_at else "",
                }
                for c in convs
            ]

        except Exception as e:
            logger.warning(f"加载用户会话列表失败: {e}")
            return []
        finally:
            try:
                await session.close()
            except Exception:
                pass

    async def update_conversation_title(
        self,
        session_id: str,
        title: str,
    ) -> bool:
        """更新会话标题（取用户第一条消息内容）。"""
        if not db_manager._enabled:
            return False

        try:
            session_iter = db_manager.get_session()
            session = await anext(session_iter)
        except RuntimeError:
            return False

        try:
            await session.execute(
                update(Conversation)
                .where(Conversation.session_id == session_id)
                .values(title=title[:256])
            )
            await session.commit()
            return True
        except Exception as e:
            await session.rollback()
            logger.warning(f"更新会话标题失败: {e}")
            return False
        finally:
            try:
                await session.close()
            except Exception:
                pass

    async def delete_conversation(
        self,
        session_id: str,
        user_id: int,
    ) -> bool:
        """删除 MySQL 中的会话及其所有消息。

        Phase 6 bugfix: 与 checkpointer 同步删除，解决「删除后重新登录又出现」的问题。
        """
        if not db_manager._enabled:
            return False

        try:
            session_iter = db_manager.get_session()
            session = await anext(session_iter)
        except RuntimeError:
            return False

        try:
            # 1. 查找会话
            result = await session.execute(
                select(Conversation).where(
                    Conversation.session_id == session_id,
                    Conversation.user_id == user_id,
                )
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                logger.debug(f"要删除的会话不存在: {session_id}")
                return True  # 幂等：已经不存在了，也算成功

            conversation_id = conversation.id

            # 2. 删除所有消息
            await session.execute(
                text("DELETE FROM messages WHERE conversation_id = :cid"),
                {"cid": conversation_id},
            )

            # 3. 删除会话记录
            await session.delete(conversation)

            await session.commit()
            logger.info(f"已从 MySQL 删除会话: {session_id} (user={user_id})")
            return True

        except Exception as e:
            await session.rollback()
            logger.warning(f"MySQL 删除会话失败: {e}")
            return False
        finally:
            try:
                await session.close()
            except Exception:
                pass


# 模块级单例
message_store = MessageStore()
