"""RAG Agent 静态检测方法

提供代码修改检测、诊断查询检测、来源提取、工具提取等纯函数。
从 RagAgentService 中提取为独立模块，减少主文件行数。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.documents import Document
    from langchain_core.messages import BaseMessage, HumanMessage, AIMessage


def code_was_changed(messages: list["BaseMessage"]) -> bool:
    """检测本轮对话是否涉及代码修改。

    三重启发式检测（任一命中即返回 True）：
    1. user 消息中包含明确的代码修改意图关键词
    2. assistant 回答中包含代码块（```）—— 且非纯 shell 命令
    3. 消息中引用了 .py 文件路径

    注意：启发式 #2 仅检查 assistant 消息（Agent 生成了代码），
    不检查 user 消息——用户在问题中贴代码块 ≠ 要求修改代码。
    """
    from langchain_core.messages import HumanMessage, AIMessage

    # 代码修改意图关键词（精确匹配，避免常见词误判）
    code_intent_keywords = [
        "修改代码", "改代码", "修复代码", "fix code",
        "添加函数", "添加方法", "添加类", "创建文件",
        "refactor", "重构",
        "写一个", "写代码", "帮我写",
        "implement",
    ]

    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""

        # ① 检查代码修改意图（仅 user 消息）
        if isinstance(msg, HumanMessage):
            content_lower = content.lower()
            if any(kw in content_lower for kw in code_intent_keywords):
                return True

        # ② 检查 assistant 是否生成了代码块（排除纯 shell/bash 块）
        if isinstance(msg, AIMessage) and "```" in content:
            # 排除纯安装/配置命令（```bash ```, ```sh ``` 等）
            code_blocks = re.findall(r"```(\w*)\n(.*?)```", content, re.DOTALL)
            if code_blocks:
                for lang, code in code_blocks:
                    if lang.lower() not in ("bash", "sh", "shell", "console", "cmd"):
                        return True
                # 全是 bash/shell 类代码块 → 不是真正的代码修改
            else:
                # 无标准代码块匹配 → 可能是内联 ``` 引用，不触发
                pass

        # ③ 检查是否引用了 .py 文件路径且含修改意图
        if ".py" in content and (
            "app/" in content or "修改" in content or "改" in content
        ):
            return True

    return False


def extract_sources(docs: list["Document"]) -> list[dict]:
    """从检索到的文档中提取来源信息（文件名 + 章节路径）。

    每个来源包含:
    - file_name: 来源文件名
    - headers:  层级标题列表（如 ["CPU 高负载排查", "常见原因"]）

    按文件名去重：同一文件只保留一条。
    """
    sources: list[dict] = []
    seen: set[str] = set()
    for doc in docs:
        meta = doc.metadata or {}
        file_name = meta.get("_file_name", "未知文件")
        if file_name in seen:
            continue
        seen.add(file_name)
        headers = []
        for key in ("h1", "h2", "h3"):
            if meta.get(key):
                headers.append(meta[key])
        sources.append({
            "file_name": file_name,
            "headers": headers,
        })
    return sources


def is_diagnostic_query(messages: list["BaseMessage"]) -> bool:
    """检测是否为诊断类查询（触发四层防线检查）。

    诊断查询的特征：
    1. 故障排查意图：告警、报错、异常、故障、超时、崩溃
    2. 性能分析意图：CPU、内存、磁盘、延迟、吞吐
    3. 根因分析意图：根因、原因、为什么
    4. 运维操作意图：排查、诊断、巡检、检查

    排除：纯代码修改请求（这些由 code_was_changed + FeedbackLoop 处理）
    """
    diagnostic_keywords = [
        # 故障排查
        "告警", "报警", "报错", "异常", "故障", "宕机", "崩溃",
        "超时", "timeout", "连接失败", "拒绝连接",
        "不响应", "卡死", "挂了", "起不来", "重启",
        "OOM", "out of memory", "内存溢出",
        "磁盘满", "IO高", "IO等待",
        # 性能分析
        "CPU", "cpu", "内存", "磁盘", "网络", "延迟", "latency",
        "吞吐", "throughput", "QPS", "TPS", "负载", "load",
        "响应慢", "慢查询", "高负载",
        # 根因分析
        "根因", "原因", "为什么", "怎么回事", "什么原因",
        "root cause", "排查", "诊断", "分析",
        # 运维操作
        "巡检", "检查", "监控", "指标", "metrics",
        "日志", "log", "CLS", "Prometheus",
        # 数据库中间件
        "连接池", "死锁", "慢SQL", "主从", "复制延迟",
        "Redis", "redis", "MySQL", "mysql", "PostgreSQL",
        "Nginx", "nginx", "K8s", "k8s", "Pod",
    ]

    for msg in messages:
        content = msg.content if hasattr(msg, "content") else ""
        if not content:
            continue

        content_lower = content.lower()
        for kw in diagnostic_keywords:
            if kw.lower() in content_lower:
                return True

    return False


def extract_tool_names(messages_result: list) -> list[str]:
    """从 Agent 执行结果中提取实际调用的工具名称列表。"""
    tool_names: list[str] = []
    seen: set[str] = set()

    for msg in messages_result:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                if name and name not in seen:
                    seen.add(name)
                    tool_names.append(name)

    return tool_names
