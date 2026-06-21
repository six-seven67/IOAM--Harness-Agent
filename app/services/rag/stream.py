"""RAG Agent 流式查询

包含 query_stream() 方法 —— 逐步流式返回 Agent 回答片段。
包含缓存检查、知识库检索、Agent 流式执行、验证反馈、诊断质量检查。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from loguru import logger

from app.config import config
from app.tools.knowledge_tool import format_docs
from app.agent.mcp_client import format_exception_chain
from app.services.context_manager import context_manager
from app.services.prompt_builder import prompt_builder
from app.services.message_store import message_store
from app.services.vector.store import vector_store_manager
from app.core.redis import redis_manager
from app.harness.feedback import FeedbackLoop

from app.services.rag.detector import (
    code_was_changed,
    extract_sources,
    is_diagnostic_query,
    extract_tool_names,
)


class RagStreamHandler:
    """流式查询处理器。

    从 RagAgentService 中提取，保持流式方法独立于 agent 生命周期管理。
    """

    def __init__(self, agent_service: "RagAgentService"):
        self.agent = agent_service

    async def query_stream(
        self,
        question: str,
        session_id: str,
        user_id: Optional[int] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式处理用户问题（逐步返回答案片段）"""
        full_answer_parts: list[str] = []
        conversation_id: int = -1

        try:
            # ① 加载 MCP 工具
            await self.agent._load_mcp_tools()

            agent = await self.agent._get_or_create_agent(user_id)

            logger.info(
                f"[会话 {session_id}] RAG Agent 收到查询（流式）"
                f"{', user=' + str(user_id) if user_id else ''}: {question[:80]}"
            )

            # ② 构建动态系统提示词
            system_prompt = await prompt_builder.build(
                user_id=user_id,
                session_id=session_id,
            )

            # ②.5 自动检索知识库
            retrieved_docs = vector_store_manager.similarity_search(
                question, k=config.rag_top_k
            )
            if retrieved_docs:
                self.agent._last_sources = extract_sources(retrieved_docs)
                retrieved_context = format_docs(retrieved_docs)
                system_prompt += (
                    f"\n\n## 知识库检索结果（自动检索，请基于以下内容回答）"
                    f"\n\n{retrieved_context}"
                )
                logger.info(
                    f"[会话 {session_id}] 自动检索到 {len(retrieved_docs)} 个相关文档"
                )
            else:
                self.agent._last_sources = []
                logger.info(
                    f"[会话 {session_id}] 自动检索未找到相关文档，LLM 将使用通用知识回答"
                )

            # ③ 加载历史消息
            history_messages: list[BaseMessage] = []
            if user_id is not None:
                db_history = await message_store.load_history(
                    user_id, session_id, limit=20
                )
                for m in db_history:
                    if m["role"] == "user":
                        history_messages.append(HumanMessage(content=m["content"]))
                    elif m["role"] == "assistant":
                        history_messages.append(AIMessage(content=m["content"]))

            # ④ 构建消息列表
            messages: list[BaseMessage] = [
                SystemMessage(content=system_prompt),
            ]
            if history_messages:
                messages.extend(history_messages)
            messages.append(HumanMessage(content=question))

            # ⑤ 上下文裁剪
            trimmed = context_manager.manage({"messages": messages})
            if trimmed is not None:
                trimmed_msgs = [
                    m for m in trimmed.get("messages", [])
                    if not (hasattr(m, "id") and "remove" in str(type(m).__name__).lower())
                ]
                if trimmed_msgs:
                    messages = trimmed_msgs

            # ⑥ Phase 5: 查询语义缓存 — 纯问答先查缓存
            code_changed = code_was_changed(messages)
            if not code_changed:
                cached_answer = await redis_manager.get_cached_answer(question)
                if cached_answer:
                    logger.info(
                        f"[会话 {session_id}] 缓存命中（流式），跳过 Agent 推理"
                    )
                    yield {
                        "type": "content",
                        "data": cached_answer,
                    }
                    yield {
                        "type": "complete",
                        "data": {"answer": cached_answer, "tool_calls": []},
                    }
                    # 异步持久化
                    if user_id is not None:
                        conv_id = await message_store.ensure_conversation(
                            user_id=user_id,
                            session_id=session_id,
                            title=question[:80],
                        )
                        if conv_id > 0:
                            message_store.save_message_fire_and_forget(
                                user_id=user_id, conversation_id=conv_id,
                                role="user", content=question,
                            )
                            message_store.save_message_fire_and_forget(
                                user_id=user_id, conversation_id=conv_id,
                                role="assistant", content=cached_answer,
                            )
                    return

            # ⑥.5 构建 Agent 输入
            agent_input = {"messages": messages}
            config_dict = {"configurable": {"thread_id": session_id}}

            # ⑦ 流式执行
            async for token, metadata in agent.astream(
                input=agent_input,
                config=config_dict,
                stream_mode="messages",
            ):
                node_name = (
                    metadata.get("langgraph_node", "unknown")
                    if isinstance(metadata, dict)
                    else "unknown"
                )
                message_type = type(token).__name__

                if message_type in ("AIMessage", "AIMessageChunk"):
                    content_blocks = getattr(token, "content_blocks", None)

                    if content_blocks and isinstance(content_blocks, list):
                        for block in content_blocks:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_content = block.get("text", "")
                                if text_content:
                                    full_answer_parts.append(text_content)
                                    yield {
                                        "type": "content",
                                        "data": text_content,
                                        "node": node_name,
                                    }

            full_answer = "".join(full_answer_parts)

            # ⑧.5 Phase 5: 写回查询缓存
            if not code_changed and full_answer:
                await redis_manager.set_cached_answer(question, full_answer, ttl=600)
                logger.debug(
                    f"[会话 {session_id}] 查询结果已缓存 (TTL=600s, 流式)"
                )

            # 异步持久化到 MySQL
            if user_id is not None and full_answer:
                conv_id = await message_store.ensure_conversation(
                    user_id=user_id,
                    session_id=session_id,
                    title=question[:80],
                )
                if conv_id > 0:
                    conversation_id = conv_id
                    q_tokens = context_manager.count_tokens_text(question)
                    a_tokens = context_manager.count_tokens_text(full_answer)
                    message_store.save_message_fire_and_forget(
                        user_id=user_id,
                        conversation_id=conv_id,
                        role="user",
                        content=question,
                        token_count=q_tokens,
                    )
                    message_store.save_message_fire_and_forget(
                        user_id=user_id,
                        conversation_id=conv_id,
                        role="assistant",
                        content=full_answer,
                        token_count=a_tokens,
                    )

            # ⑨ Phase 4: 验证反馈 — 代码修改时跑质量门禁
            if code_changed:
                async for chunk in self._run_code_feedback_stream(
                    session_id=session_id,
                    user_id=user_id,
                    messages=messages,
                    full_answer_parts=full_answer_parts,
                    conversation_id=conversation_id,
                    config_dict=config_dict,
                    agent=agent,
                ):
                    yield chunk

            full_answer = "".join(full_answer_parts)

            # ⑩ Phase 4.1: 诊断回答质量检查（四层防线）
            async for chunk in self._run_diagnostic_quality_stream(
                session_id=session_id,
                user_id=user_id,
                messages=messages,
                full_answer=full_answer,
                full_answer_parts=full_answer_parts,
                retrieved_docs=retrieved_docs,
                conversation_id=conversation_id,
                config_dict=config_dict,
                agent=agent,
            ):
                yield chunk

            full_answer = "".join(full_answer_parts)
            logger.info(
                f"[会话 {session_id}] RAG Agent 查询完成（流式）, "
                f"answer_len={len(full_answer)}"
            )
            yield {"type": "complete"}

        except Exception as e:
            detail = format_exception_chain(e)
            logger.error(
                f"[会话 {session_id}] RAG Agent 查询失败（流式）: {detail}"
            )
            yield {"type": "error", "data": detail}

    # ── 流式代码验证反馈 ──────────────────────────────────────────────────

    async def _run_code_feedback_stream(
        self,
        session_id: str,
        user_id: Optional[int],
        messages: list[BaseMessage],
        full_answer_parts: list[str],
        conversation_id: int,
        config_dict: dict,
        agent,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Phase 4: 流式代码修改验证反馈循环。"""
        logger.info(
            f"[会话 {session_id}] 检测到代码修改迹象，启动验证反馈循环"
        )
        for attempt in range(FeedbackLoop.MAX_RETRIES):
            result = await self.agent.feedback_loop.run(code_changed=True)
            if result.passed:
                logger.info(
                    f"[会话 {session_id}] 验证通过 (第 {attempt + 1} 轮)"
                )
                return

            feedback_msg = self.agent.feedback_loop.format_feedback(result)
            logger.warning(
                f"[会话 {session_id}] 验证未通过 (第 {attempt + 1} 轮)，"
                f"反馈给 Agent"
            )

            yield {
                "type": "content",
                "data": (
                    f"\n\n[验证反馈] 质量门禁未通过，正在修正代码 "
                    f"(第 {attempt + 1}/{FeedbackLoop.MAX_RETRIES} 轮)...\n\n"
                ),
                "node": "feedback",
            }

            fix_messages = list(messages)
            fix_messages.append(HumanMessage(content=feedback_msg))
            fix_input = {"messages": fix_messages}

            try:
                fix_result = await agent.ainvoke(
                    input=fix_input,
                    config=config_dict,
                )
                fix_result_msgs = fix_result.get("messages", [])
                if fix_result_msgs:
                    last_fix = fix_result_msgs[-1]
                    fix_answer = (
                        last_fix.content
                        if hasattr(last_fix, "content")
                        else str(last_fix)
                    )

                    yield {
                        "type": "content",
                        "data": fix_answer,
                        "node": "feedback",
                    }

                    full_answer_parts.append(fix_answer)

                    if user_id is not None and conversation_id > 0:
                        a_tokens_fix = context_manager.count_tokens_text(fix_answer)
                        message_store.save_message_fire_and_forget(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            role="assistant",
                            content=f"[修正后] {fix_answer}",
                            token_count=a_tokens_fix,
                        )
            except Exception as fix_err:
                logger.error(
                    f"[会话 {session_id}] Agent 修正失败: "
                    f"{format_exception_chain(fix_err)}"
                )
                yield {
                    "type": "error",
                    "data": f"代码修正失败: {format_exception_chain(fix_err)}",
                }
                return
        else:
            logger.warning(
                f"[会话 {session_id}] 验证反馈耗尽 {FeedbackLoop.MAX_RETRIES} 轮重试"
            )

    # ── 流式诊断质量检查 ──────────────────────────────────────────────────

    async def _run_diagnostic_quality_stream(
        self,
        session_id: str,
        user_id: Optional[int],
        messages: list[BaseMessage],
        full_answer: str,
        full_answer_parts: list[str],
        retrieved_docs: list | None,
        conversation_id: int,
        config_dict: dict,
        agent,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Phase 4.1: 流式诊断回答质量检查（四层防线）。"""
        is_diag = is_diagnostic_query(messages)
        if not is_diag or not full_answer:
            return

        logger.info(
            f"[会话 {session_id}] 诊断类查询（流式），启动回答质量检查"
        )
        actual_tool_names: list[str] = []

        for attempt in range(self.agent.RESPONSE_QUALITY_MAX_RETRIES):
            full_answer = "".join(full_answer_parts)
            quality_result = self.agent.response_harness.check(
                answer=full_answer,
                documents=retrieved_docs if retrieved_docs else None,
                is_diagnostic=True,
                tool_names=actual_tool_names,
            )

            if quality_result.passed:
                logger.info(
                    f"[会话 {session_id}] 诊断质量检查通过 "
                    f"(第 {attempt + 1} 轮)"
                )
                return

            quality_feedback = self.agent.response_harness.format_feedback(
                quality_result
            )
            logger.warning(
                f"[会话 {session_id}] 诊断质量检查未通过 "
                f"(第 {attempt + 1} 轮): {quality_result.failures}"
            )

            yield {
                "type": "content",
                "data": (
                    f"\n\n[诊断质量反馈] 回答质量检查未通过，"
                    f"正在修正 (第 {attempt + 1}/"
                    f"{self.agent.RESPONSE_QUALITY_MAX_RETRIES} 轮)...\n\n"
                ),
                "node": "quality_feedback",
            }

            fix_messages = list(messages)
            fix_messages.append(HumanMessage(content=quality_feedback))
            fix_input = {"messages": fix_messages}

            try:
                fix_result = await agent.ainvoke(
                    input=fix_input,
                    config=config_dict,
                )
                fix_result_msgs = fix_result.get("messages", [])
                if fix_result_msgs:
                    last_fix = fix_result_msgs[-1]
                    fix_answer = (
                        last_fix.content
                        if hasattr(last_fix, "content")
                        else str(last_fix)
                    )

                    yield {
                        "type": "content",
                        "data": fix_answer,
                        "node": "quality_feedback",
                    }

                    full_answer_parts.append(fix_answer)
                    actual_tool_names = extract_tool_names(fix_result_msgs)

                    if user_id is not None and conversation_id > 0:
                        a_tokens_fix = context_manager.count_tokens_text(fix_answer)
                        message_store.save_message_fire_and_forget(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            role="assistant",
                            content=f"[诊断质量修正] {fix_answer}",
                            token_count=a_tokens_fix,
                        )
            except Exception as fix_err:
                logger.error(
                    f"[会话 {session_id}] Agent 诊断修正失败: "
                    f"{format_exception_chain(fix_err)}"
                )
                yield {
                    "type": "error",
                    "data": f"诊断质量修正失败: {format_exception_chain(fix_err)}",
                }
                return
        else:
            logger.warning(
                f"[会话 {session_id}] 诊断质量修正耗尽 "
                f"{self.agent.RESPONSE_QUALITY_MAX_RETRIES} 轮重试"
            )


# Pre-import stub for type hint resolution
from app.services.rag.agent import RagAgentService
