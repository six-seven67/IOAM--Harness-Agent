"""动态提示词组装器

实现四层上下文管理的 Layer 4（动态 AGENTS.md 注入）。

在 AGENTS.md 基础上动态追加运行时上下文，组装成完整的系统提示词。

五层加载模型：
  ① 基础契约 — AGENTS.md（始终注入，~500 tokens）
  ② 长期记忆 — MEMORY.md 最近内容（始终注入，~400 tokens）
  ③ 用户偏好 — 从 MySQL users 表动态加载（可选，~200 tokens）
  ④ 会话摘要 — 从 ContextManager 获取（可选，~300 tokens）
  ⑤ 工具列表 — 由 LangGraph 框架自动注入（无需管理）

使用方式：
    from app.services.prompt_builder import prompt_builder

    system_prompt = await prompt_builder.build(user_id=1, session_id="abc")
"""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger

from app.config import config


class PromptBuilder:
    """动态提示词组装器。

    启动时读取 AGENTS.md 和 MEMORY.md 文件，
    每次 build() 时动态层叠用户偏好和会话上下文。
    """

    # ── 文件路径常量 ─────────────────────────────────────────────────────

    AGENTS_MD_PATH = "context/AGENTS.md"
    MEMORY_MD_PATH = "context/MEMORY.md"

    # ── 初始化 ───────────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._agents_md: Optional[str] = None
        self._memory_md: Optional[str] = None
        self._load_files()

    def _load_files(self) -> None:
        """启动时加载 AGENTS.md 和 MEMORY.md。"""
        self._agents_md = self._read_file(self.AGENTS_MD_PATH)
        self._memory_md = self._read_file(self.MEMORY_MD_PATH)

        if self._agents_md:
            logger.info(f"已加载 {self.AGENTS_MD_PATH} ({len(self._agents_md)} 字符)")
        else:
            logger.warning(f"{self.AGENTS_MD_PATH} 不存在，将使用默认系统提示词")

        if self._memory_md:
            # 只取最近 2000 字符控制 token 预算
            truncated = self._memory_md[-2000:] if len(self._memory_md) > 2000 else self._memory_md
            self._memory_md = truncated
            logger.info(f"已加载 {self.MEMORY_MD_PATH} ({len(self._memory_md)} 字符有效)")
        else:
            logger.debug(f"{self.MEMORY_MD_PATH} 不存在，跳过长期记忆注入")

    def reload(self) -> None:
        """热加载：重新读取 AGENTS.md 和 MEMORY.md。

        用于运行时更新 Agent 契约或长期记忆，无需重启服务。
        """
        self._load_files()
        logger.info("提示词文件已热加载")

    # ── 公共方法 ─────────────────────────────────────────────────────────

    async def build(
        self,
        user_id: Optional[int] = None,
        session_id: str = "",
        user_prefs: Optional[str] = None,
        session_summary: Optional[str] = None,
    ) -> str:
        """组装完整的系统提示词。

        Args:
            user_id: 用户 ID（用于加载偏好）
            session_id: 会话 ID（保留接口，未来扩展）
            user_prefs: 用户偏好文本（外部加载后传入，避免循环依赖）
            session_summary: 会话上下文摘要（从 ContextManager 获取）

        Returns:
            str: 完整的系统提示词（直接传给 LLM）
        """
        sections: list[str] = []

        # ① 基础契约（AGENTS.md）
        if self._agents_md:
            sections.append(self._agents_md)
        else:
            sections.append(self._default_system_prompt())

        # ② 长期记忆（MEMORY.md）
        if self._memory_md:
            sections.append(
                f"\n## 长期记忆（跨会话知识积累）\n\n{self._memory_md}"
            )

        # ③ 用户偏好（外部传入，避免访问 DB）
        if user_prefs:
            sections.append(f"\n## 用户偏好\n\n{user_prefs}")

        # ④ 会话上下文摘要（外部传入）
        if session_summary:
            sections.append(f"\n## 当前会话上下文\n\n{session_summary}")

        # ⑤ 模型能力提示（始终包含）
        sections.append(
            f"\n## 模型信息\n\n"
            f"当前模型: {config.dashscope_model}\n"
            f"上下文窗口: 32K tokens\n"
            f"知识截止: 训练数据截止日期前的信息"
        )

        return "\n\n".join(sections)

    # ── 辅助方法 ─────────────────────────────────────────────────────────

    def _read_file(self, path: str) -> Optional[str]:
        """安全读取文件，不存在时返回 None。"""
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.warning(f"读取 {path} 失败: {e}")
            return None

    def _default_system_prompt(self) -> str:
        """默认系统提示词（AGENTS.md 不存在时的回退）。

        与 Phase 1/2 的硬编码提示词保持一致。
        """
        from textwrap import dedent

        return dedent("""
            你是一个专业的AI助手，能够使用多种工具来帮助用户解决问题。

            工作原则:
            1. 理解用户需求，选择合适的工具来完成任务
            2. 当需要获取实时信息或专业知识时，主动使用相关工具
            3. 基于工具返回的结果提供准确、专业的回答
            4. 如果工具无法提供足够信息，请诚实地告知用户

            回答要求:
            - 保持友好、专业的语气
            - 回答简洁明了，重点突出
            - 基于事实，不编造信息
            - 如有不确定的地方，明确说明

            请根据用户的问题，灵活使用可用工具，提供高质量的帮助。
        """).strip()


# 模块级单例
prompt_builder = PromptBuilder()
