"""RAG Agent 非流式查询

包含 query() 方法 —— 处理用户问题并返回完整答案。
包含缓存检查、知识库检索、Agent 执行、验证反馈、诊断质量检查。
"""

from __future__ import annotations

from typing import Optional

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


class RagQueryHandler:
    """非流式查询处理器。

    从 RagAgentService 中提取，保持查询方法独立于 agent 生命周期管理。
    """

    def __init__(self, agent_service: "RagAgentService"):
        self.agent = agent_service

    async def query(
        self,
        question: str,
        session_id: str,
        user_id: Optional[int] = None,
    ) -> str:
        """非流式处理用户问题。"""
        try:
            # ① 加载 MCP 工具（首次调用）
            await self.agent._load_mcp_tools()

            agent = await self.agent._get_or_create_agent(user_id)

            logger.info(
                f"[会话 {session_id}] RAG Agent 收到查询（非流式）"
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

            # ③ 加载历史消息（从 MySQL）
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

            # ⑤ 上下文裁剪（预裁剪，避免首次调用就超限）
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
            cached_answer: Optional[str] = None
            if not code_changed:
                cached_answer = await redis_manager.get_cached_answer(question)
                if cached_answer:
                    logger.info(
                        f"[会话 {session_id}] 缓存命中，跳过 Agent 推理"
                    )
                    # 缓存命中时仍需异步持久化
                    if user_id is not None:
                        conv_id = await message_store.ensure_conversation(
                            user_id=user_id,
                            session_id=session_id,
                            title=question[:80],
                        )
                        if conv_id > 0:
                            message_store.save_message_fire_and_forget(
                                user_id=user_id,
                                conversation_id=conv_id,
                                role="user",
                                content=question,
                                token_count=context_manager.count_tokens_text(question),
                            )
                            message_store.save_message_fire_and_forget(
                                user_id=user_id,
                                conversation_id=conv_id,
                                role="assistant",
                                content=cached_answer,
                                token_count=context_manager.count_tokens_text(cached_answer),
                            )
                    return cached_answer

            # ⑥.5 构建 Agent 输入并执行
            agent_input = {"messages": messages}
            config_dict = {"configurable": {"thread_id": session_id}}

            result = await agent.ainvoke(
                input=agent_input,
                config=config_dict,
            )

            # ⑦ 提取最终答案
            messages_result = result.get("messages", [])
            answer = ""
            if messages_result:
                last_message = messages_result[-1]
                answer = (
                    last_message.content
                    if hasattr(last_message, "content")
                    else str(last_message)
                )

                if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                    tool_names = [
                        tc.get("name", "unknown")
                        for tc in last_message.tool_calls
                    ]
                    logger.info(
                        f"[会话 {session_id}] Agent 调用了工具: {tool_names}"
                    )

            # ⑦.5 Phase 5: 写回查询缓存（纯问答 + 本轮无缓存命中）
            if not code_changed and answer:
                await redis_manager.set_cached_answer(question, answer, ttl=600)
                logger.debug(
                    f"[会话 {session_id}] 查询结果已缓存 (TTL=600s)"
                )

            # ⑧ 异步持久化消息到 MySQL（fire-and-forget）
            conv_id = -1
            if user_id is not None:
                conv_id = await message_store.ensure_conversation(
                    user_id=user_id,
                    session_id=session_id,
                    title=question[:80],
                )
                if conv_id > 0:
                    q_tokens = context_manager.count_tokens_text(question)
                    a_tokens = context_manager.count_tokens_text(answer)
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
                        content=answer,
                        token_count=a_tokens,
                    )

            # ⑨ Phase 4: 验证反馈 — 如果涉及代码修改，跑质量门禁
            if code_changed:
                await self._run_code_feedback_loop(
                    session_id=session_id,
                    user_id=user_id,
                    messages=messages,
                    messages_result=messages_result,
                    answer=answer,
                    conv_id=conv_id,
                    config_dict=config_dict,
                    agent=agent,
                )

            # ⑩ Phase 4.1: 诊断回答质量检查（四层防线）
            answer = await self._run_diagnostic_quality_check(
                session_id=session_id,
                user_id=user_id,
                messages=messages,
                messages_result=messages_result,
                answer=answer,
                retrieved_docs=retrieved_docs,
                conv_id=conv_id,
                config_dict=config_dict,
                agent=agent,
            )

            logger.info(
                f"[会话 {session_id}] RAG Agent 查询完成（非流式）, "
                f"answer_len={len(answer)}"
            )
            return answer

        except Exception as e:
            logger.error(
                f"[会话 {session_id}] RAG Agent 查询失败（非流式）: "
                f"{format_exception_chain(e)}"
            )
            raise

    # ── 代码验证反馈循环 ──────────────────────────────────────────────────

    async def _run_code_feedback_loop(
        self,
        session_id: str,
        user_id: Optional[int],
        messages: list[BaseMessage],
        messages_result: list,
        answer: str,
        conv_id: int,
        config_dict: dict,
        agent,
    ) -> list:
        """Phase 4: 代码修改后的验证反馈循环。"""
        logger.info(
            f"[会话 {session_id}] 检测到代码修改迹象，启动验证反馈循环"
        )
        for attempt in range(FeedbackLoop.MAX_RETRIES):
            result = await self.agent.feedback_loop.run(code_changed=True)
            if result.passed:
                logger.info(
                    f"[会话 {session_id}] 验证通过 (第 {attempt + 1} 轮)"
                )
                break

            feedback_msg = self.agent.feedback_loop.format_feedback(result)
            logger.warning(
                f"[会话 {session_id}] 验证未通过 (第 {attempt + 1} 轮)，"
                f"反馈给 Agent"
            )

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
                    new_answer = (
                        last_fix.content
                        if hasattr(last_fix, "content")
                        else str(last_fix)
                    )

                    if user_id is not None and conv_id > 0:
                        a_tokens_fix = context_manager.count_tokens_text(new_answer)
                        message_store.save_message_fire_and_forget(
                            user_id=user_id,
                            conversation_id=conv_id,
                            role="assistant",
                            content=f"[修正后] {new_answer}",
                            token_count=a_tokens_fix,
                        )
                    return fix_result_msgs
            except Exception as fix_err:
                logger.error(
                    f"[会话 {session_id}] Agent 修正失败: "
                    f"{format_exception_chain(fix_err)}"
                )
                break
        else:
            logger.warning(
                f"[会话 {session_id}] 验证反馈耗尽 "
                f"{FeedbackLoop.MAX_RETRIES} 轮重试"
            )
        return messages_result

    # ── 诊断质量检查 ──────────────────────────────────────────────────────

    async def _run_diagnostic_quality_check(
        self,
        session_id: str,
        user_id: Optional[int],
        messages: list[BaseMessage],
        messages_result: list,
        answer: str,
        retrieved_docs: list | None,
        conv_id: int,
        config_dict: dict,
        agent,
    ) -> str:
        """Phase 4.1: 诊断回答质量检查（四层防线）。"""
        is_diag = is_diagnostic_query(messages)
        if not is_diag or not answer:
            return answer

        logger.info(
            f"[会话 {session_id}] 诊断类查询，启动回答质量检查"
        )
        actual_tool_names = extract_tool_names(messages_result)

        for attempt in range(self.agent.RESPONSE_QUALITY_MAX_RETRIES):
            quality_result = self.agent.response_harness.check(
                answer=answer,
                documents=retrieved_docs if retrieved_docs else None,
                is_diagnostic=True,
                tool_names=actual_tool_names,
            )

            if quality_result.passed:
                logger.info(
                    f"[会话 {session_id}] 诊断质量检查通过 "
                    f"(第 {attempt + 1} 轮)"
                )
                break

            quality_feedback = self.agent.response_harness.format_feedback(
                quality_result
            )
            logger.warning(
                f"[会话 {session_id}] 诊断质量检查未通过 "
                f"(第 {attempt + 1} 轮): {quality_result.failures}"
            )

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
                    new_answer = (
                        last_fix.content
                        if hasattr(last_fix, "content")
                        else str(last_fix)
                    )
                    answer = new_answer
                    actual_tool_names = extract_tool_names(fix_result_msgs)

                    if user_id is not None and conv_id > 0:
                        a_tokens_fix = context_manager.count_tokens_text(new_answer)
                        message_store.save_message_fire_and_forget(
                            user_id=user_id,
                            conversation_id=conv_id,
                            role="assistant",
                            content=f"[诊断质量修正] {new_answer}",
                            token_count=a_tokens_fix,
                        )
            except Exception as fix_err:
                logger.error(
                    f"[会话 {session_id}] Agent 诊断修正失败: "
                    f"{format_exception_chain(fix_err)}"
                )
                break
        else:
            logger.warning(
                f"[会话 {session_id}] 诊断质量修正耗尽 "
                f"{self.agent.RESPONSE_QUALITY_MAX_RETRIES} 轮重试"
            )

        return answer


# Pre-import stub for type hint resolution
from app.services.rag.agent import RagAgentService
