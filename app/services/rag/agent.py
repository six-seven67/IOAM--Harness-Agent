"""RAG Agent 核心生命周期

管理 LLM 模型、checkpointer、agent 实例缓存、MCP 工具加载、
会话历史、清理等。查询逻辑在 query.py 和 stream.py 中。

Phase 3: 按用户隔离的 SqliteSaver + 动态系统提示词 + MySQL 持久化
Phase 4: 集成 FeedbackLoop + MemoryManager
Phase 4.1: ResponseHarness 四层防线
Phase 5: 查询语义缓存集成
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator, Dict, Optional

import aiosqlite
from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_qwq import ChatQwen
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from loguru import logger

from app.config import config
from app.tools import DEFAULT_LOCAL_AGENT_TOOLS
from app.agent.mcp_client import (
    get_mcp_client_with_retry,
    load_mcp_tools_safe,
    format_exception_chain,
    suggest_mcp_transport,
)
from app.services.context_manager import context_manager
from app.services.prompt_builder import prompt_builder
from app.services.message_store import message_store
from app.services.vector.store import vector_store_manager
from app.core.redis import redis_manager
from app.harness.validator import Validator
from app.harness.feedback import FeedbackLoop, FeedbackResult, ResponseHarness, ResponseQualityResult
from app.harness.memory import MemoryManager
from app.harness.hallucination import hallucination_gate
from app.harness.evidence import evidence_scorer
from app.harness.sop import sop_checker
from app.harness.confidence import confidence_assessor
from app.harness.feedback import ResponseQualityResult as _ResponseQualityResult

from app.services.rag.detector import (
    code_was_changed,
    extract_sources,
    is_diagnostic_query,
    extract_tool_names,
)


class RagAgentService:
    """RAG Agent 服务 - 使用 LangGraph + ChatQwen 原生集成。

    Phase 3 升级：
    - 按用户隔离的 SqliteSaver（登录用户持久化，匿名用户内存）
    - 动态系统提示词（AGENTS.md + MEMORY.md）
    - MySQL 消息持久化（异步，不阻塞对话）
    - Token 感知上下文裁剪（ContextManager）

    Phase 4 升级：
    - 验证反馈循环（代码修改后自动跑 ruff/mypy/pytest）
    - 长期记忆写入（诊断经验自动追加到 MEMORY.md）

    Phase 4.1 升级（四层防线）：
    - 诊断回答后自动检查：幻觉检测 + 证据链 + SOP合规 + 置信度
    - 不通过 → 生成反馈 → Agent 修正 → 最多 2 轮

    Phase 5 升级：
    - 查询语义缓存（Redis，纯问答 10-min TTL）
    """

    def __init__(self, streaming: bool = True):
        self.model_name = config.rag_model
        self.streaming = streaming

        # LLM 模型（全局共享）
        self.model = ChatQwen(
            model=self.model_name,
            api_key=config.dashscope_api_key,
            temperature=0.7,
            streaming=streaming,
        )

        # 本地工具（全局共享）
        self.tools = list(DEFAULT_LOCAL_AGENT_TOOLS)

        # MCP 工具（延迟加载）
        self.mcp_tools: list = []
        self._mcp_loaded = False

        # 匿名用户回退 checkpointer（MemorySaver）
        self._memory_checkpointer = MemorySaver()

        # 按用户缓存的 agent 和 checkpointer
        self._agents: Dict[Optional[int], Any] = {}
        self._checkpointers: Dict[Optional[int], Any] = {}

        # ── Phase 4: 验证反馈系统（代码质量）────────────────────────────────
        self.validator = Validator()
        self.feedback_loop = FeedbackLoop(self.validator)
        self.memory = MemoryManager()

        # ── Phase 4.1: 诊断回答质量检查（四层防线）──────────────────────────
        self.response_harness = ResponseHarness(
            hallucination_gate=hallucination_gate,
            evidence_scorer=evidence_scorer,
            sop_checker=sop_checker,
            confidence_assessor=confidence_assessor,
        )
        self.RESPONSE_QUALITY_MAX_RETRIES = 2

        # 最近一次检索的来源文档（供 API 层获取，展示给前端）
        self._last_sources: list[dict] = []

        logger.info(
            f"RAG Agent 服务初始化完成 (ChatQwen), model={self.model_name}, "
            f"streaming={streaming}"
        )

    # ─────────────────── MCP 工具加载（全局一次）───────────────────────────

    async def _load_mcp_tools(self):
        """加载 MCP 工具（全局只加载一次，所有用户共享）。"""
        if self._mcp_loaded:
            return

        for name, server in config.mcp_servers.items():
            hint = suggest_mcp_transport(
                str(server.get("url", "")),
                str(server.get("transport", "")),
            )
            if hint:
                logger.warning(f"MCP 配置 [{name}]: {hint}")

        mcp_client = await get_mcp_client_with_retry()
        mcp_tools, mcp_err = await load_mcp_tools_safe(mcp_client)
        if mcp_err:
            logger.warning(
                f"MCP 工具加载失败，将仅使用本地工具继续运行:\n{mcp_err}"
            )
            self.mcp_tools = []
        else:
            self.mcp_tools = mcp_tools
            logger.info(f"成功加载 {len(mcp_tools)} 个 MCP 工具")

        self._mcp_loaded = True

        all_tools = self.tools + self.mcp_tools
        if all_tools:
            tool_names = [
                tool.name if hasattr(tool, "name") else str(tool)
                for tool in all_tools
            ]
            logger.info(f"可用工具列表: {', '.join(tool_names)}")

    # ─────────────────── Checkpointer（按用户隔离）──────────────────────────

    async def _get_checkpointer(self, user_id: Optional[int]):
        """获取用户专属的 checkpointer。

        - user_id=None（匿名）→ MemorySaver（不持久化）
        - user_id=int（登录用户）→ AsyncSqliteSaver（持久化）
        """
        if user_id is None:
            return self._memory_checkpointer

        if user_id not in self._checkpointers:
            db_dir = "data/checkpoints"
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, f"user_{user_id}.db")
            conn = await aiosqlite.connect(db_path)
            self._checkpointers[user_id] = AsyncSqliteSaver(conn)
            await self._checkpointers[user_id].setup()
            logger.info(f"创建用户 {user_id} 的 SQLite 检查点: {db_path}")

        return self._checkpointers[user_id]

    # ─────────────────── Agent（按用户缓存）────────────────────────────────

    async def _get_or_create_agent(self, user_id: Optional[int]):
        """获取或创建用户专属的 LangGraph agent。"""
        if user_id not in self._agents:
            checkpointer = await self._get_checkpointer(user_id)
            all_tools = self.tools + self.mcp_tools
            self._agents[user_id] = create_agent(
                self.model,
                tools=all_tools,
                checkpointer=checkpointer,
            )
            checkpointer_type = (
                "SqliteSaver" if user_id is not None else "MemorySaver"
            )
            logger.info(
                f"创建用户 {user_id or '匿名'} 的 Agent "
                f"(checkpointer={checkpointer_type}, tools={len(all_tools)})"
            )
        return self._agents[user_id]

    # ─────────────────── 来源文档 ──────────────────────────────────────────

    def get_last_sources(self) -> list[dict]:
        """返回最近一次检索的来源文档列表（供 API 层使用）。"""
        return self._last_sources

    # ─────────────────── 会话历史（从 LangGraph checkpointer）──────────────

    async def get_session_history(
        self,
        session_id: str,
        user_id: Optional[int] = None,
    ) -> list:
        """获取会话历史。读取降级链: checkpointer → MySQL → 空列表。

        Phase 6 bugfix: 当 checkpointer 为空时（跨设备登录、clear_session 后），
        从 MySQL MessageStore fallback 读取，确保历史对话可点击展开。
        """
        # 第一层：LangGraph checkpointer（内存/SQLite）
        try:
            checkpointer = await self._get_checkpointer(user_id)
            config = {"configurable": {"thread_id": session_id}}

            checkpoint_tuple = await checkpointer.aget(config)

            if checkpoint_tuple:
                if hasattr(checkpoint_tuple, "checkpoint"):
                    checkpoint_data = checkpoint_tuple.checkpoint
                else:
                    checkpoint_data = (
                        checkpoint_tuple[0] if checkpoint_tuple else {}
                    )

                messages = (
                    checkpoint_data.get("channel_values", {}).get("messages", [])
                )

                history = []
                for msg in messages:
                    if isinstance(msg, SystemMessage):
                        continue
                    role = "user" if isinstance(msg, HumanMessage) else "assistant"
                    content = msg.content if hasattr(msg, "content") else str(msg)
                    from datetime import datetime
                    history.append({
                        "role": role,
                        "content": content,
                        "timestamp": datetime.now().isoformat(),
                    })

                if history:
                    logger.info(
                        f"获取会话历史(checkpointer): {session_id}, "
                        f"消息数量: {len(history)}"
                    )
                    return history

        except Exception as e:
            logger.warning(f"从 checkpointer 读取历史失败: {e}")

        # 第二层：MySQL MessageStore fallback
        if user_id is not None:
            try:
                mysql_history = await message_store.load_history(
                    session_id, limit=50
                )
                if mysql_history:
                    logger.info(
                        f"获取会话历史(MySQL fallback): {session_id}, "
                        f"消息数量: {len(mysql_history)}"
                    )
                    return mysql_history
            except Exception as e:
                logger.warning(f"从 MySQL 读取历史失败: {e}")

        logger.info(f"获取会话历史: {session_id}, 消息数量: 0")
        return []

    async def clear_session(
        self,
        session_id: str,
        user_id: Optional[int] = None,
    ) -> bool:
        """清空会话历史（checkpointer + MySQL 双删）。

        Phase 6 bugfix: 之前只删 checkpointer，MySQL 中数据原封不动，
        导致用户重新登录后「已删除」的会话从 load_user_conversations 复活。
        """
        success = True
        # 1. 删除 checkpointer 中的会话状态
        try:
            checkpointer = await self._get_checkpointer(user_id)
            await checkpointer.adelete_thread(session_id)
            logger.info(f"已从 checkpointer 清除会话: {session_id}")
        except Exception as e:
            logger.error(f"checkpointer 清空会话失败: {session_id}, 错误: {e}")
            success = False

        # 2. 同步删除 MySQL 中的会话和消息
        if user_id is not None:
            try:
                mysql_ok = await message_store.delete_conversation(
                    session_id, user_id
                )
                if not mysql_ok:
                    logger.warning(
                        f"MySQL 删除会话失败（checkpointer 已删）: {session_id}"
                    )
            except Exception as e:
                logger.error(f"MySQL 删除会话异常: {session_id}, 错误: {e}")

        return success

    # ─────────────────── 查询入口（委托给 handler）──────────────────────────

    async def query(
        self,
        question: str,
        session_id: str,
        user_id: Optional[int] = None,
    ) -> str:
        """非流式处理用户问题。委托给 RagQueryHandler。"""
        from app.services.rag.query import RagQueryHandler
        handler = RagQueryHandler(self)
        return await handler.query(question, session_id, user_id)

    async def query_stream(
        self,
        question: str,
        session_id: str,
        user_id: Optional[int] = None,
    ) -> "AsyncGenerator[Dict[str, Any], None]":
        """流式处理用户问题。委托给 RagStreamHandler。"""
        from app.services.rag.stream import RagStreamHandler
        handler = RagStreamHandler(self)
        async for chunk in handler.query_stream(question, session_id, user_id):
            yield chunk

    # ─────────────────── 资源清理 ─────────────────────────────────────────

    async def cleanup(self):
        """清理资源：关闭所有 SQLite 连接。"""
        try:
            logger.info("清理 RAG Agent 服务资源...")
            for user_id, checkpointer in self._checkpointers.items():
                if hasattr(checkpointer, "conn") and checkpointer.conn:
                    checkpointer.conn.close()
                    logger.debug(f"已关闭用户 {user_id} 的 SQLite 连接")
            self._checkpointers.clear()
            self._agents.clear()
            logger.info("RAG Agent 服务资源已清理")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")

    # ─────────────────── 共享辅助 ─────────────────────────────────────────
    # 这些方法保持对旧代码的兼容（作为实例方法委托给 detector 模块）

    @staticmethod
    def _code_was_changed(messages: list[BaseMessage]) -> bool:
        """检测本轮对话是否涉及代码修改。委托给 detector.code_was_changed。"""
        return code_was_changed(messages)

    @staticmethod
    def _extract_sources(docs):
        """提取来源信息。委托给 detector.extract_sources。"""
        return extract_sources(docs)

    @staticmethod
    def _is_diagnostic_query(messages):
        """检测诊断查询。委托给 detector.is_diagnostic_query。"""
        return is_diagnostic_query(messages)

    @staticmethod
    def _extract_tool_names(messages_result):
        """提取工具名称。委托给 detector.extract_tool_names。"""
        return extract_tool_names(messages_result)


# 全局单例，默认启用流式输出
rag_agent_service = RagAgentService(streaming=True)
