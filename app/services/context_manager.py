"""智能上下文管理器

替换现有的 trim_messages_middleware，实现四层上下文管理的 Layer 3（Token 感知裁剪）。

核心职责：
1. 使用 tiktoken 精确计算消息 token 数
2. 双重阈值监控（消息轮次 + token 总量）
3. 触发裁剪时：LLM 摘要旧消息 + 保留最近轮次原文
4. 作为 LangGraph middleware 集成到 Agent 中

设计原则：
- 不是简单丢弃旧消息，而是用 LLM 摘要保留关键信息
- 双重阈值确保 token 预算不会意外超支
- 摘要内容对 LLM 友好：保留上下文，释放 token 空间

使用方式：
    from app.services.context_manager import ContextManager

    ctx_manager = ContextManager()
    # 作为 LangGraph middleware:
    agent = create_agent(model, tools, checkpointer=..., middleware=[ctx_manager.manage])
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import tiktoken
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from loguru import logger

from app.config import config


class ContextManager:
    """智能上下文管理器 — Token 感知裁剪 + LLM 摘要。

    同时监控消息轮次（rounds）和 token 总量两个维度，
    任一超过阈值时触发裁剪：旧消息 → LLM 摘要 → SystemMessage 注入，
    最近轮次原文保留。

    配置项（从 config 读取，可运行时覆盖）：
        ctx_max_tokens:      token 上限（默认 8000）
        ctx_trim_trigger:    触发压缩的比例（默认 0.80）
        ctx_max_rounds:      最大保留轮次（默认 12）
        ctx_keep_recent:     裁剪时保留最近轮次（默认 6）
        ctx_summary_model:   摘要模型名（默认 "qwen-max"）
    """

    # ── 阈值属性（从 config 读取）────────────────────────────────────────

    @property
    def max_tokens(self) -> int:
        return config.ctx_max_tokens

    @property
    def trim_trigger(self) -> float:
        return config.ctx_trim_trigger

    @property
    def max_rounds(self) -> int:
        return config.ctx_max_rounds

    @property
    def keep_recent(self) -> int:
        return config.ctx_keep_recent

    @property
    def summary_model(self) -> str:
        return config.ctx_summary_model

    # ── 公共入口：LangGraph middleware 签名 ──────────────────────────────

    def manage(self, state: dict) -> dict | None:
        """LangGraph middleware 入口。

        签名兼容 LangGraph prebuilt create_agent 的 middleware 参数：
        - 接收当前 state（含 messages）
        - 返回 None（无需裁剪）或 dict（裁剪后的 messages 更新）

        流程：
        1. 计算 token 数和轮次数
        2. 判断是否需要裁剪（双重阈值）
        3. 如需裁剪 → 拆分旧/新消息 → LLM 摘要旧消息 → 重建消息列表
        4. 返回 LangGraph 可识别的更新格式
        """
        messages: Sequence[BaseMessage] = state.get("messages", [])

        if len(messages) <= 3:
            return None  # 消息太少，不裁剪

        # 分离系统消息和对话消息
        system_msgs: List[BaseMessage] = []
        conv_msgs: List[BaseMessage] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                system_msgs.append(m)
            else:
                conv_msgs.append(m)

        # 计算指标
        token_count = self._count_tokens(conv_msgs)
        round_count = len(conv_msgs) // 2  # user + assistant = 1 轮

        token_threshold = int(self.max_tokens * self.trim_trigger)

        needs_trim_rounds = round_count > self.max_rounds
        needs_trim_tokens = token_count > token_threshold

        if not needs_trim_rounds and not needs_trim_tokens:
            logger.debug(
                f"上下文无需裁剪: {round_count} 轮, {token_count} tokens "
                f"(阈值: {self.max_rounds} 轮 / {token_threshold} tokens)"
            )
            return None

        # 决定保留多少轮
        keep = self.keep_recent
        if needs_trim_tokens:
            # token 超标时进一步减少保留轮次
            ratio = token_threshold / max(token_count, 1)
            keep = max(3, int(round_count * ratio))

        keep = min(keep, round_count)

        # 拆分：旧消息（将被摘要） + 最近消息（保留原文）
        split_idx = -(keep * 2)  # 每轮 2 条消息（user + assistant）
        old_messages = conv_msgs[:split_idx] if split_idx != 0 else []
        recent_messages = conv_msgs[split_idx:] if split_idx != 0 else conv_msgs

        logger.info(
            f"上下文裁剪触发: {round_count} 轮 / {token_count} tokens → "
            f"保留最近 {keep} 轮 ({len(recent_messages)} 条), "
            f"摘要 {len(old_messages)} 条旧消息"
        )

        # 重建消息列表
        new_messages: List[BaseMessage] = []

        # ① 系统消息（始终保留）
        new_messages.extend(system_msgs)

        # ② 旧消息摘要（如果有需要摘要的旧消息）
        if old_messages:
            summary_text = self._summarize_sync(old_messages)
            summary_msg = SystemMessage(
                content=(
                    f"[对话历史摘要 — 以下为更早对话的关键信息]\n"
                    f"{summary_text}\n"
                    f"[/对话历史摘要]"
                )
            )
            new_messages.append(summary_msg)

        # ③ 最近消息（保留原文）
        new_messages.extend(recent_messages)

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
            ]
        }

    # ── Token 计数 ───────────────────────────────────────────────────────

    def _count_tokens(self, messages: Sequence[BaseMessage]) -> int:
        """计算消息列表的总 token 数。

        使用 cl100k_base 编码（GPT-4/3.5 tokenizer），
        与 Qwen 的 tokenizer 近似（误差 < 5%），
        用于预算控制已足够精确。

        返回：所有消息 content 的 token 总和
        """
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            logger.warning("tiktoken 编码加载失败，回退到字符数估算")
            return self._estimate_tokens(messages)

        total = 0
        for msg in messages:
            content = msg.content if hasattr(msg, "content") else str(msg)
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                # content 可能是多模态 blocks 列表
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        total += len(enc.encode(block["text"]))
            # 每条消息附加 ~4 tokens 的元数据开销
            total += 4
        return total

    def _estimate_tokens(self, messages: Sequence[BaseMessage]) -> int:
        """token 数估算（tiktoken 不可用时的回退方案）。

        中文约 1.5 字符/token，英文约 4 字符/token。
        取保守估算 2 字符/token。
        """
        total_chars = 0
        for msg in messages:
            content = msg.content if hasattr(msg, "content") else str(msg)
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        total_chars += len(block["text"])
        return max(1, total_chars // 2)

    def count_tokens_text(self, text: str) -> int:
        """公开工具方法：计算单段文本的 token 数。

        用于外部（如 MessageStore）计算消息 token。
        """
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return max(1, len(text) // 2)

    # ── LLM 摘要生成 ─────────────────────────────────────────────────────

    def _summarize_sync(self, messages: Sequence[BaseMessage]) -> str:
        """同步生成对话摘要（用于 middleware 同步上下文）。

        注意：LangGraph middleware 是同步的，
        因此这里调用同步 LLM（如果不可用则回退到简单裁剪）。

        摘要格式（中文，简洁）：
        - 用户主要问题
        - 已获取的关键信息
        - 已完成的操作步骤
        """
        # 构建摘要提示词
        conversation_text = self._format_messages_for_summary(messages)

        if not conversation_text.strip():
            return "（无内容）"

        try:
            # 尝试同步调用 LLM
            from langchain_qwq import ChatQwen

            llm = ChatQwen(
                model=self.summary_model,
                api_key=config.dashscope_api_key,
                temperature=0.3,  # 低温度，确保摘要稳定
                streaming=False,
            )

            prompt = (
                "请用中文简洁概括以下对话的核心内容（控制在 150 字以内）：\n"
                "需包含：用户主要问题、已获取的关键信息、已完成的操作步骤。\n\n"
                "对话：\n"
                f"{conversation_text}\n\n"
                "摘要："
            )

            response = llm.invoke(prompt)
            summary = response.content if hasattr(response, "content") else str(response)
            logger.debug(f"摘要生成成功 ({len(summary)} 字符)")
            return summary.strip()

        except Exception as e:
            logger.warning(f"LLM 摘要生成失败，回退到简单裁剪: {e}")
            return self._fallback_summary(messages)

    def _fallback_summary(self, messages: Sequence[BaseMessage]) -> str:
        """回退摘要（无 LLM 时）：提取每条消息的首句。

        这不是理想的摘要，但比完全丢弃旧消息好。
        """
        lines: List[str] = []
        for msg in messages:
            role = "用户" if isinstance(msg, HumanMessage) else "AI"
            content = msg.content if hasattr(msg, "content") else str(msg)
            if isinstance(content, str) and content.strip():
                # 取前 80 字符作为摘要片段
                snippet = content[:80].replace("\n", " ")
                lines.append(f"[{role}] {snippet}...")
            elif isinstance(content, list):
                text_parts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and "text" in b
                ]
                if text_parts:
                    snippet = " ".join(text_parts)[:80].replace("\n", " ")
                    lines.append(f"[{role}] {snippet}...")

        if not lines:
            return "（无关键信息）"
        return "\n".join(lines[:10])  # 最多 10 条摘要行

    def _format_messages_for_summary(
        self, messages: Sequence[BaseMessage]
    ) -> str:
        """将消息序列格式化为摘要提示词可用的文本。"""
        parts: List[str] = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                continue  # 跳过系统消息（已经在新消息列表中）
            role = "用户" if isinstance(msg, HumanMessage) else (
                "工具返回" if isinstance(msg, ToolMessage) else "AI"
            )
            content = msg.content if hasattr(msg, "content") else str(msg)
            if isinstance(content, str):
                # 截断过长消息
                text = content[:300] + ("..." if len(content) > 300 else "")
                parts.append(f"{role}: {text}")
            elif isinstance(content, list):
                text_parts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and "text" in b
                ]
                text = " ".join(text_parts)[:300]
                parts.append(f"{role}: {text}")
        return "\n".join(parts)


# 模块级单例
context_manager = ContextManager()
