"""SOP 合规检查 (Layer 3 — 反馈防线)

检查 Agent 诊断过程是否遵循标准操作流程 (SOP)：
1. 诊断是否按 Planner → Executor → Replanner 走完？
2. 是否跳过了必要步骤（如未查知识库直接猜）？
3. 是否在工具失败时给出了明确的替代方案？

SOP 定义（来自 AGENTS.md 诊断工作流）：
    用户告警 → retrieve_knowledge(查SOP) → query_monitor(查指标) → query_cls(查日志)
                                                                   ↓
    用户 ← 诊断报告（含证据链）  ← 根因分析 ← 汇总证据

核心原则：
- 纯模式匹配，零 API 开销
- 检测流程跳跃（跳过关键步骤 → 标记）
- 只在诊断场景触发
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger


@dataclass
class SopCheckResult:
    """SOP 合规检查结果

    Attributes:
        passed: 是否通过
        stages_found: 检测到的 SOP 阶段列表
        stages_expected: 期望的 SOP 阶段列表
        missing_stages: 缺失的关键阶段
        tool_calls_count: 工具调用次数（从外部传入的消息历史中统计）
        skipped: 是否跳过（非诊断场景）
        detail: 人类可读的简短摘要
    """
    passed: bool
    stages_found: list[str] = field(default_factory=list)
    stages_expected: list[str] = field(default_factory=list)
    missing_stages: list[str] = field(default_factory=list)
    tool_calls_count: int = 0
    skipped: bool = False
    detail: str = ""


class SopChecker:
    """SOP 合规检查器 —— 诊断过程是否走完了标准流程。

    使用方法:
        checker = SopChecker()
        result = checker.check(
            answer=agent_answer,
            is_diagnostic=True,
            tool_names=["retrieve_knowledge", "query_monitor"],
        )
    """

    # ── SOP 阶段定义 ──────────────────────────────────────────────────────

    # 诊断回答应包含的阶段（按顺序）
    EXPECTED_STAGES: list[tuple[str, str]] = [
        # (阶段名, 检测关键词)
        ("知识检索",   "(?:检索|查询|搜索|查阅).*(?:知识库|文档|SOP|手册|经验)|"
                      "(?:知识库|文档|SOP|手册).*(?:检索|查询|显示|记录|指出|提到)"),
        ("数据采集",   "(?:监控|指标|metrics|Prometheus|Grafana|CLS|日志|log|alert|告警)"
                      ".*(?:显示|返回|表明|输出|发现|查询|采集)"),
        ("分析推理",   "(?:根据|基于|依据|分析|发现|排查).+?(?:因此|所以|由此|可以判断|根因|原因|导致)"),
        ("根因结论",   "(?:根因|根本原因|root.?cause|问题本质).{0,30}(?:是|为|在于|确定为)"),
        ("行动建议",   "(?:建议|推荐|方案|措施|步骤|操作|执行|修复|处理|解决|恢复)"),
    ]

    # ── 流程跳跃检测 ──────────────────────────────────────────────────────

    # 如果回答直接给出了结论但没有任何检索/数据采集的迹象，标记为跳跃
    DIRECT_CONCLUSION_WITHOUT_EVIDENCE: list[str] = [
        r"^(?:根因|问题|原因)(?:是|为|在于)",
        r"^(?:直接|立刻|马上).{0,10}(?:结论|判断)",
    ]

    # ── 强制步骤 ──────────────────────────────────────────────────────────

    # 必须出现的阶段（至少一个）
    MANDATORY_STAGES = ["知识检索", "数据采集"]

    # ── 公开方法 ──────────────────────────────────────────────────────────

    def check(
        self,
        answer: str,
        is_diagnostic: bool = False,
        tool_names: list[str] | None = None,
    ) -> SopCheckResult:
        """执行 SOP 合规检查。

        Args:
            answer: Agent 生成的回答文本
            is_diagnostic: 是否为诊断类查询
            tool_names: Agent 实际调用的工具名称列表（可选，增强检测精度）

        Returns:
            SopCheckResult: 检查结果
        """
        import re
        if not is_diagnostic:
            logger.debug("[SOP] 非诊断场景，跳过")
            return SopCheckResult(passed=True, skipped=True)

        tool_names = tool_names or []
        tool_calls_count = len(tool_names)

        # 如果 Agent 完全没有调用任何工具，且回答很短 → 跳过（非诊断场景）
        if tool_calls_count == 0 and len(answer) < 200:
            logger.debug("[SOP] 无工具调用且回答简短，跳过")
            return SopCheckResult(passed=True, skipped=True)

        # ① 检测各 SOP 阶段
        stages_found: list[str] = []
        for stage_name, pattern in self.EXPECTED_STAGES:
            if __import__("re").search(pattern, answer, re.IGNORECASE):
                stages_found.append(stage_name)

        expected = [s[0] for s in self.EXPECTED_STAGES]

        # ② 检测缺失的关键阶段
        missing: list[str] = []
        for mandatory in self.MANDATORY_STAGES:
            if mandatory not in stages_found:
                missing.append(mandatory)

        # ③ 检测流程跳跃（有结论但无检索/数据）
        has_conclusion = "根因结论" in stages_found
        has_evidence = "知识检索" in stages_found or "数据采集" in stages_found
        jumped = False
        if has_conclusion and not has_evidence:
            for pattern in self.DIRECT_CONCLUSION_WITHOUT_EVIDENCE:
                if re.search(pattern, answer, re.IGNORECASE):
                    jumped = True
                    if "流程跳跃" not in missing:
                        missing.append("流程跳跃（无证据直接给结论）")
                    break

        # ④ 工具调用补偿：如果 Agent 实际调用了工具，但文本中没体现
        # 说明 Agent 可能在思考过程中使用了工具但没在回答中引用
        if tool_names:
            for tool in tool_names:
                tool_stage = self._map_tool_to_stage(tool)
                if tool_stage and tool_stage not in stages_found:
                    stages_found.append(f"{tool_stage}(工具调用)")

        # ⑤ 判定
        passed = len(missing) == 0

        detail_parts: list[str] = []
        if passed:
            detail_parts.append("SOP 阶段完整")
        else:
            detail_parts.append(f"缺失: {', '.join(missing)}")

        if tool_calls_count > 0:
            detail_parts.append(f"工具调用 {tool_calls_count} 次")

        detail = "; ".join(detail_parts) if detail_parts else "通过"

        if passed:
            logger.info(
                f"[SOP] 通过 — 检测到阶段: {stages_found}, "
                f"工具调用: {tool_calls_count} 次"
            )
        else:
            logger.warning(
                f"[SOP] 未通过 — 缺失阶段: {missing}, "
                f"检测到: {stages_found}"
            )

        return SopCheckResult(
            passed=passed,
            stages_found=stages_found,
            stages_expected=expected,
            missing_stages=missing,
            tool_calls_count=tool_calls_count,
            detail=detail,
        )

    # ── 内部方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _map_tool_to_stage(tool_name: str) -> str | None:
        """将工具名映射到 SOP 阶段。"""
        mapping: dict[str, str] = {
            "retrieve_knowledge": "知识检索",
            "vector_search": "知识检索",
            "knowledge_search": "知识检索",
            "query_monitor": "数据采集",
            "query_metrics": "数据采集",
            "query_cls": "数据采集",
            "cls_search_log": "数据采集",
            "query_logs": "数据采集",
            "replanner": "分析推理",
            "replan": "分析推理",
            "execute_diagnosis": "行动建议",
        }
        for key, stage in mapping.items():
            if key in tool_name.lower():
                return stage
        return None

    # ── 反馈生成 ──────────────────────────────────────────────────────────

    @staticmethod
    def format_feedback(result: SopCheckResult) -> str:
        """将 SOP 检查结果格式化为 Agent 可理解的反馈文本。"""
        if result.passed or result.skipped:
            return ""

        lines = ["[验证反馈 — SOP 合规] 诊断流程未完整走完标准操作流程：", ""]

        for stage in result.missing_stages:
            if "知识检索" in stage:
                lines.append(
                    "- **未检索知识库**：请先使用 `retrieve_knowledge` 工具查询"
                    "已有的 SOP 和历史诊断经验，再做判断"
                )
            elif "数据采集" in stage:
                lines.append(
                    "- **未采集实时数据**：请使用监控/日志工具获取当前运行数据，"
                    "不要仅凭历史经验推断"
                )
            elif "流程跳跃" in stage:
                lines.append(
                    "- **跳过诊断流程直接给结论**：请按 Planner → Executor → "
                    "Replanner 流程执行，先查知识库和采集数据，再推理根因"
                )
            else:
                lines.append(f"- **缺失阶段**: {stage}")

        lines.extend([
            "",
            f"期望流程: {' → '.join(result.stages_expected)}",
            f"实际检测: {result.stages_found or '无'}",
            "请按标准流程重新执行诊断。",
        ])
        return "\n".join(lines)


# 模块级单例
sop_checker = SopChecker()
