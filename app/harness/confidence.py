"""诊断置信度评估 (Layer 3 — 反馈防线)

评估 Agent 诊断结论的置信度是否与其证据基础匹配：
1. 证据充分时：结论可以坚定
2. 证据不足时：必须明确说"无法确定"或使用适当的模糊表达
3. 禁止在没有证据时装作很确定（这是最危险的幻觉形式）

与 evidence.py 的区别：
- evidence.py 检查"有没有引用证据"（结构完整性）
- confidence.py 检查"结论语气是否与证据量匹配"（语义适当性）

核心原则：
- 纯关键词 + 模式匹配，零 API 开销
- 惩罚过度自信（强断言 + 弱证据 = 危险组合）
- 只在诊断场景触发
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class ConfidenceResult:
    """置信度评估结果

    Attributes:
        passed: 是否通过（语气与证据匹配）
        confidence_level: 检测到的置信度级别 ("high" | "medium" | "low" | "unknown")
        hedging_markers: 检测到的模糊表达词数量（"可能"、"或许"等）
        certainty_markers: 检测到的确定性表达词数量（"确定"、"肯定"等）
        uncertainty_statement: 是否明确说了"无法确定"或类似表述
        overconfident: 是否检测到过度自信（强断言 + 弱证据）
        risk_flags: 风险标记列表
        skipped: 是否跳过
        detail: 人类可读摘要
    """
    passed: bool
    confidence_level: str = "unknown"
    hedging_markers: int = 0
    certainty_markers: int = 0
    uncertainty_statement: bool = False
    overconfident: bool = False
    risk_flags: list[str] = field(default_factory=list)
    skipped: bool = False
    detail: str = ""


class ConfidenceAssessor:
    """诊断置信度评估器。

    使用方法:
        assessor = ConfidenceAssessor()
        result = assessor.assess(
            answer=agent_answer,
            is_diagnostic=True,
            evidence_score=0.6,  # 来自 EvidenceScorer
        )
    """

    # ── 阈值 ──────────────────────────────────────────────────────────────

    # 证据评分 < LOW_EVIDENCE_THRESHOLD 视为"弱证据"
    LOW_EVIDENCE_THRESHOLD = 0.4

    # 确定性标志词 ≥ HIGH_CERTAINTY_THRESHOLD 且证据弱 → 过度自信
    HIGH_CERTAINTY_THRESHOLD = 2

    # ── 模糊表达词（适当的 hedging）────────────────────────────────────────

    HEDGING_MARKERS: list[str] = [
        "可能", "或许", "也许", "大概", "似乎", "疑似",
        "初步判断", "初步分析", "初步排查",
        "待确认", "待验证", "需要进一步", "还需排查",
        "之一", "不排除", "也有可能是",
        "暂不确定", "尚不明确",
        "建议进一步", "建议排查",
        "might", "may", "could", "possibly", "likely",
        "probably", "seems", "appears",
    ]

    # ── 确定性表达词（强断言）─────────────────────────────────────────────

    CERTAINTY_MARKERS: list[str] = [
        "确定", "肯定", "绝对", "毫无疑问", "很明显",
        "正是", "就是这个", "一定是", "必定", "必然",
        "100%", "百分之百",
        "definitely", "certainly", "absolutely", "undoubtedly",
        "exactly", "precisely",
    ]

    # ── 不确定性声明（正面标志）───────────────────────────────────────────

    UNCERTAINTY_STATEMENTS: list[str] = [
        "无法确定", "无法判断", "不能确定",
        "信息不足", "证据不足", "数据不足",
        "需要更多信息", "需要更多数据",
        "有待进一步", "暂时无法",
        "不确定", "难以判断",
    ]

    # ── 过度自信检测模式 ──────────────────────────────────────────────────

    # 弱证据下的强断言 → 高风险
    OVERCONFIDENCE_PATTERNS: list[tuple[str, str]] = [
        # (风险类型, 检测正则)
        (
            "无证据根因断言",
            r"(?:根因|根本原因).{0,20}(?:是|为|在于|确定为).{0,30}(?:[，。\n]|$)"
        ),
        (
            "无证据排除",
            r"(?:排除|不是|不可能).{0,20}(?:了|的|原因|问题)"
        ),
        (
            "无证据保证",
            r"(?:只要|只需|肯定|保证).{0,30}(?:就|能|可以|恢复|解决|修复)"
        ),
    ]

    # ── 公开方法 ──────────────────────────────────────────────────────────

    def assess(
        self,
        answer: str,
        is_diagnostic: bool = False,
        evidence_score: float | None = None,
    ) -> ConfidenceResult:
        """评估诊断结论的置信度。

        Args:
            answer: Agent 生成的回答文本
            is_diagnostic: 是否为诊断类查询
            evidence_score: 证据链评分（来自 EvidenceScorer，0.0~1.0）

        Returns:
            ConfidenceResult: 评估结果
        """
        if not is_diagnostic:
            logger.debug("[置信度] 非诊断场景，跳过")
            return ConfidenceResult(passed=True, skipped=True)

        if not answer or len(answer.strip()) < 50:
            logger.debug("[置信度] 回答过短，跳过")
            return ConfidenceResult(passed=True, skipped=True)

        # ① 统计模糊表达
        hedging = self._count_markers(answer, self.HEDGING_MARKERS)

        # ② 统计确定性表达
        certainty = self._count_markers(answer, self.CERTAINTY_MARKERS)

        # ③ 检测不确定性声明（正面标志）
        has_uncertainty = any(
            kw in answer for kw in self.UNCERTAINTY_STATEMENTS
        )

        # ④ 判定置信度级别
        if certainty > hedging and certainty >= self.HIGH_CERTAINTY_THRESHOLD:
            confidence_level = "high"
        elif hedging > certainty:
            confidence_level = "low"
        else:
            confidence_level = "medium"

        # ⑤ 过度自信检测（弱证据 + 高确定 = 危险）
        overconfident = False
        risk_flags: list[str] = []

        # 如果提供了外部证据评分
        if evidence_score is not None and evidence_score < self.LOW_EVIDENCE_THRESHOLD:
            if confidence_level == "high":
                overconfident = True
                risk_flags.append(
                    f"证据链评分 {evidence_score:.0%} (低) 但结论语气坚定 —— "
                    f"过度自信风险"
                )

        # 无论是否有证据评分，检测绝对化表述
        if certainty >= self.HIGH_CERTAINTY_THRESHOLD:
            # 检查是否有任何证据引用
            has_any_source = bool(
                re.search(r"```", answer)
                or re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", answer)
                or re.search(r"""(?:日志|监控|指标|文档|检索).*?(?:显示|返回|发现|记录|指出)""", answer)
            )
            if not has_any_source:
                overconfident = True
                risk_flags.append(
                    f"强断言 ({certainty} 个确定性表达) 但无数据来源引用"
                )

        # ⑥ 模式检测过度自信
        if evidence_score is not None and evidence_score < 0.5:
            for risk_type, pattern in self.OVERCONFIDENCE_PATTERNS:
                matches = re.findall(pattern, answer, re.IGNORECASE)
                if matches:
                    overconfident = True
                    for m in matches[:3]:
                        risk_flags.append(f"[{risk_type}] {m.strip()[:60]}")

        # ⑦ 判定
        passed = not overconfident

        if overconfident:
            # 但如果明确说了"无法确定"，则不算过度自信
            if has_uncertainty:
                passed = True
                overconfident = False
                risk_flags.clear()

        # ⑧ 生成摘要
        detail_parts: list[str] = [
            f"置信度: {confidence_level}",
            f"模糊词: {hedging}",
            f"确定词: {certainty}",
        ]
        if has_uncertainty:
            detail_parts.append("已声明不确定性")
        if evidence_score is not None:
            detail_parts.append(f"证据: {evidence_score:.0%}")
        detail = "; ".join(detail_parts)

        if passed:
            logger.info(f"[置信度] 通过 — {detail}")
        else:
            logger.warning(
                f"[置信度] 未通过 — 过度自信, 风险: {risk_flags}"
            )

        return ConfidenceResult(
            passed=passed,
            confidence_level=confidence_level,
            hedging_markers=hedging,
            certainty_markers=certainty,
            uncertainty_statement=has_uncertainty,
            overconfident=overconfident,
            risk_flags=risk_flags,
            detail=detail,
        )

    # ── 内部方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _count_markers(text: str, markers: list[str]) -> int:
        """统计标志词在文本中的出现次数。"""
        count = 0
        for marker in markers:
            count += len(re.findall(re.escape(marker), text, re.IGNORECASE))
        return count

    # ── 反馈生成 ──────────────────────────────────────────────────────────

    @staticmethod
    def format_feedback(result: ConfidenceResult) -> str:
        """将置信度评估结果格式化为 Agent 可理解的反馈文本。"""
        if result.passed or result.skipped:
            return ""

        lines = [
            "[验证反馈 — 置信度] 诊断结论的置信度与证据基础不匹配：",
            "",
        ]

        for flag in result.risk_flags:
            lines.append(f"- ⚠️ {flag}")

        lines.extend([
            "",
            "请根据实际证据水平调整结论语气：",
            "- 证据充分 → 可以给出坚定结论",
            "- 证据不足 → 使用「可能」「疑似」「初步判断」等表达",
            "- 无法判断 → 明确说「当前信息不足，无法确定根因」",
            "",
            f"当前: 确定性表达 {result.certainty_markers} 处, "
            f"模糊表达 {result.hedging_markers} 处 "
            f"(置信度级别: {result.confidence_level})",
        ])

        if not result.uncertainty_statement:
            lines.append("⚠️ 未声明不确定性（建议在证据不足时说明「无法确定」）")

        return "\n".join(lines)


# 模块级单例
confidence_assessor = ConfidenceAssessor()
