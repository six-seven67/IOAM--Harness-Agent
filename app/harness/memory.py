"""MEMORY.md 读写管理

诊断成功后自动提取可复用模式写入 MEMORY.md。
启动时加载最近 2000 字符注入系统提示词。

与 PromptBuilder 协作：
- MemoryManager 是 **写手**（AIOps 诊断完成后追加经验）
- PromptBuilder 是 **读手**（每次会话启动时加载 MEMORY.md 内容注入 prompt）
"""

import os
from loguru import logger


class MemoryManager:
    """MEMORY.md 文件读写管理器

    职责:
    1. 启动时提供最近 2000 字符给 PromptBuilder（load_context）
    2. 诊断成功后追加新模式（record），自动去重
    """

    def __init__(self, path: str = "context/MEMORY.md"):
        """初始化 MemoryManager

        Args:
            path: MEMORY.md 文件路径，默认为 context/ 目录
        """
        self.path = path

    def load_context(self) -> str:
        """启动时加载 MEMORY.md 内容（截取最近 2000 字符控制 token）

        PromptBuilder 调用此方法获取长期记忆注入到 system prompt。

        Returns:
            str: MEMORY.md 最近 2000 字符内容，文件不存在时返回空字符串
        """
        if not os.path.exists(self.path):
            logger.debug(f"MEMORY.md 不存在: {self.path}")
            return ""

        with open(self.path, "r", encoding="utf-8") as f:
            content = f.read()

        if len(content) > 2000:
            logger.debug(
                f"MEMORY.md 长度 {len(content)} 字符，截取最近 2000 字符"
            )
            return content[-2000:]

        logger.debug(f"MEMORY.md 已加载: {len(content)} 字符")
        return content

    def record(self, symptom: str, root_cause: str, steps: str) -> bool:
        """诊断成功后追加新模式（自动去重）。

        去重策略：按 "### {symptom}" 标题判断，相同症状+根因不重复记录。

        Args:
            symptom: 故障症状描述（如 "CPU > 90%"）
            root_cause: 根因分析（如 "数据库连接池耗尽"）
            steps: 关键排查路径（如 "查连接池 → 查慢查询 → 查并发量"）

        Returns:
            bool: True 表示成功写入新模式，False 表示已存在（去重跳过）
        """
        existing = ""
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                existing = f.read()

        # 去重：相同症状标题不重复记录
        key = f"### {symptom}"
        if key in existing:
            logger.info(f"模式已存在，跳过: {symptom}")
            return False

        entry = (
            f"\n{key}\n"
            f"- **根因**: {root_cause}\n"
            f"- **排查路径**: {steps}\n"
        )

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry)

        logger.info(f"新模式已写入 MEMORY.md: {symptom}")
        return True
