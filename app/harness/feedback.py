"""验证反馈循环

Agent 完成任务后运行验证。不通过则把错误反馈给 Agent 修正。
最多重试 3 轮，防止死循环。

Phase 4.1 升级：新增 ResponseHarness（诊断回答质量四道防线检查编排器）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from app.harness.validator import Validator
    from app.harness.hallucination import (
        HallucinationGate,
        HallucinationCheckResult,
    )
    from app.harness.evidence import EvidenceScorer, EvidenceCheckResult
    from app.harness.sop import SopChecker, SopCheckResult
    from app.harness.confidence import ConfidenceAssessor, ConfidenceResult


class FeedbackStatus(Enum):
    """单次检查的通过/失败状态"""
    PASS = "pass"
    FAIL = "fail"


@dataclass
class Feedback:
    """单次质量门禁检查的结果

    Attributes:
        status: 通过 (PASS) 或失败 (FAIL)
        step: 检查步骤名称（ruff / mypy / pytest）
        detail: 一行摘要（"通过" / "exit=1" / "超时" / "命令不可用"）
        fix_hint: 失败时的错误详情（截取最后 15 行 stderr/stdout）
    """
    status: FeedbackStatus
    step: str
    detail: str = ""
    fix_hint: str = ""


@dataclass
class FeedbackResult:
    """完整验证反馈循环的结果

    Attributes:
        passed: 所有检查是否全部通过
        checks: 最后一轮（或通过轮）的逐项检查结果
        retry_count: 实际重试次数（0-based，0 = 第一轮通过）
    """
    passed: bool
    checks: list[Feedback] = field(default_factory=list)
    retry_count: int = 0

    @property
    def summary(self) -> str:
        """生成人类可读的验证结果摘要"""
        lines = []
        for c in self.checks:
            icon = "✅" if c.status == FeedbackStatus.PASS else "❌"
            lines.append(f"{icon} {c.step}: {c.detail}")
        return "\n".join(lines)


class FeedbackLoop:
    """验证反馈循环 — 只做一件事：跑验证，不通过就反馈

    调用方（RagAgentService）负责：
    1. 调用 run() 获取结果
    2. 如果不通过，用 format_feedback() 生成反馈文本
    3. 把反馈文本作为新消息发给 Agent，让 Agent 修正
    4. 重新调用 run() 验证
    5. 最多 3 轮
    """

    MAX_RETRIES = 3

    def __init__(self, validator: "Validator"):
        self.validator = validator

    async def run(self, code_changed: bool = False) -> FeedbackResult:
        """执行验证。

        当 code_changed=False 时直接跳过（返回 passed=True），零开销。

        Args:
            code_changed: 本轮对话是否涉及代码修改

        Returns:
            FeedbackResult: 包含通过状态和详细结果
        """
        if not code_changed:
            return FeedbackResult(passed=True)

        checks: list[Feedback] = []

        for attempt in range(self.MAX_RETRIES):
            checks = await self.validator.run_all()

            if all(c.status == FeedbackStatus.PASS for c in checks):
                logger.info(f"验证通过 (第 {attempt + 1} 轮)")
                return FeedbackResult(
                    passed=True,
                    checks=checks,
                    retry_count=attempt,
                )

            logger.warning(
                f"验证未通过 (第 {attempt + 1}/{self.MAX_RETRIES} 轮): "
                f"{[c.step for c in checks if c.status == FeedbackStatus.FAIL]}"
            )

            # 不可修复的错误 → 不重试，避免浪费 API 调用
            # 例如：ruff/mypy/pytest 未安装，或配置文件缺失
            unfixable = [
                c for c in checks
                if c.status == FeedbackStatus.FAIL
                and c.detail in ("工具未安装", "命令不可用")
            ]
            if unfixable and len(unfixable) == len(
                [c for c in checks if c.status == FeedbackStatus.FAIL]
            ):
                logger.warning(
                    f"验证失败原因为工具不可用，跳过重试: "
                    f"{[c.step for c in unfixable]}"
                )
                break

        # 耗尽重试，返回最后一次的失败详情
        return FeedbackResult(
            passed=False,
            checks=checks,
            retry_count=attempt + 1,
        )

    def format_feedback(self, result: FeedbackResult) -> str:
        """将验证失败信息格式化为 Agent 能理解的反馈文本

        Args:
            result: 验证结果

        Returns:
            str: 格式化的 Markdown 反馈文本，可直接作为 HumanMessage 发送给 Agent
        """
        failures = [c for c in result.checks if c.status == FeedbackStatus.FAIL]
        if not failures:
            return ""

        lines = [
            "[验证反馈] 以下质量门禁检查未通过，请修正代码后重新验证：",
            "",
        ]
        for f in failures:
            lines.append(f"## {f.step}")
            lines.append(f"问题: {f.detail}")
            if f.fix_hint:
                lines.append(f"错误详情:\n```\n{f.fix_hint}\n```")
            lines.append("")

        lines.append(
            f"重试次数: {result.retry_count}/{self.MAX_RETRIES}\n"
            f"请根据以上错误信息修正代码。"
        )
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Phase 4.1: ResponseHarness — 诊断回答质量检查编排器
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ResponseQualityResult:
    """诊断回答质量综合检查结果

    聚合四道防线的检查结果（Layer 1 幻觉检测 + Layer 3 证据链/SOP/置信度）。

    Attributes:
        passed: 所有检查是否全部通过
        hallucination: 幻觉检测结果（Layer 1）
        evidence: 证据链评分结果（Layer 3）
        sop: SOP 合规检查结果（Layer 3）
        confidence: 置信度评估结果（Layer 3）
        failures: 未通过的检查项名称列表
        skipped: 是否全部跳过（非诊断场景）
    """
    passed: bool
    hallucination: "HallucinationCheckResult | None" = None
    evidence: "EvidenceCheckResult | None" = None
    sop: "SopCheckResult | None" = None
    confidence: "ConfidenceResult | None" = None
    failures: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def summary(self) -> str:
        """生成人类可读的检查结果摘要。"""
        lines: list[str] = []
        checks = [
            ("幻觉检测", self.hallucination),
            ("证据链",   self.evidence),
            ("SOP合规",  self.sop),
            ("置信度",   self.confidence),
        ]
        for label, result in checks:
            if result is None:
                lines.append(f"⊘ {label}: 未运行")
            elif getattr(result, "skipped", False):
                lines.append(f"⊘ {label}: 跳过")
            elif getattr(result, "passed", False):
                lines.append(f"✅ {label}: 通过")
            else:
                lines.append(f"❌ {label}: 未通过 ({getattr(result, 'detail', '')})")
        return "\n".join(lines)


class ResponseHarness:
    """诊断回答质量编排器 —— 串联四道防线检查 Agent 的回答质量。

    与 FeedbackLoop（代码质量）互补：
    - FeedbackLoop 检查 Agent 写的**代码**有没有 lint/type/test 问题
    - ResponseHarness 检查 Agent 写的**诊断文本**是否可信、完整、合规

    检查顺序（按成本从低到高）：
    1. 置信度评估  — 最便宜的文本模式匹配
    2. SOP 合规    — 检查流程完整性
    3. 证据链评分  — 检查引用和推理链
    4. 幻觉检测    — 交叉验证知识库（需要检索结果，成本最高）

    使用方法:
        from app.harness.feedback import ResponseHarness
        from app.harness import (
            hallucination_gate, evidence_scorer,
            sop_checker, confidence_assessor,
        )

        harness = ResponseHarness(
            hallucination_gate=hallucination_gate,
            evidence_scorer=evidence_scorer,
            sop_checker=sop_checker,
            confidence_assessor=confidence_assessor,
        )

        result = harness.check(
            answer=agent_answer,
            documents=retrieved_docs,
            is_diagnostic=True,
            tool_names=["retrieve_knowledge", "query_monitor"],
        )

        if not result.passed:
            feedback = harness.format_feedback(result)
            # 把 feedback 发给 Agent 让其修正
    """

    def __init__(
        self,
        hallucination_gate: "HallucinationGate",
        evidence_scorer: "EvidenceScorer",
        sop_checker: "SopChecker",
        confidence_assessor: "ConfidenceAssessor",
    ):
        self.hallucination_gate = hallucination_gate
        self.evidence_scorer = evidence_scorer
        self.sop_checker = sop_checker
        self.confidence_assessor = confidence_assessor

    def check(
        self,
        answer: str,
        documents: list | None = None,
        is_diagnostic: bool = False,
        tool_names: list[str] | None = None,
    ) -> ResponseQualityResult:
        """串联运行全部四道防线检查。

        Args:
            answer: Agent 生成的回答全文
            documents: 知识库检索结果（用于幻觉检测交叉验证）
            is_diagnostic: 是否为诊断类查询（False 时全部跳过）
            tool_names: Agent 实际调用的工具名称列表（增强 SOP 检测精度）

        Returns:
            ResponseQualityResult: 综合检查结果
        """
        if not is_diagnostic:
            logger.debug("[ResponseHarness] 非诊断场景，全部跳过")
            return ResponseQualityResult(passed=True, skipped=True)

        if not answer or len(answer.strip()) < 50:
            logger.debug("[ResponseHarness] 回答过短，跳过")
            return ResponseQualityResult(passed=True, skipped=True)

        # ── 1. 置信度评估（最便宜，先跑）─────────────────────────────────
        conf_result = self.confidence_assessor.assess(
            answer=answer,
            is_diagnostic=is_diagnostic,
            evidence_score=None,  # 先跑，还没证据评分
        )

        # ── 2. SOP 合规检查 ───────────────────────────────────────────────
        sop_result = self.sop_checker.check(
            answer=answer,
            is_diagnostic=is_diagnostic,
            tool_names=tool_names,
        )

        # ── 3. 证据链评分 ─────────────────────────────────────────────────
        evidence_result = self.evidence_scorer.evaluate(
            answer=answer,
            is_diagnostic=is_diagnostic,
        )

        # ── 4. 幻觉检测（最贵：需要检索结果）─────────────────────────────
        hallu_result = self.hallucination_gate.check(
            answer=answer,
            documents=documents,
        )

        # ── 5. 二次置信度评估（用证据链评分校准）─────────────────────────
        # 如果证据链弱但第一次未检测到过度自信，用证据评分重新评估
        # LOW_EVIDENCE_THRESHOLD = 0.4 from ConfidenceAssessor
        if (
            conf_result.passed
            and not evidence_result.skipped
            and evidence_result.score < 0.4
        ):
            conf_result = self.confidence_assessor.assess(
                answer=answer,
                is_diagnostic=is_diagnostic,
                evidence_score=evidence_result.score,
            )

        # ── 6. 汇总 ──────────────────────────────────────────────────────
        failures: list[str] = []
        for label, result in [
            ("hallucination", hallu_result),
            ("evidence", evidence_result),
            ("sop", sop_result),
            ("confidence", conf_result),
        ]:
            if result is not None and not getattr(result, "skipped", True):
                if not getattr(result, "passed", True):
                    failures.append(label)

        passed = len(failures) == 0

        if passed:
            logger.info("[ResponseHarness] 全部通过 ✅")
        else:
            logger.warning(
                f"[ResponseHarness] 未通过: {failures}"
            )

        return ResponseQualityResult(
            passed=passed,
            hallucination=hallu_result,
            evidence=evidence_result,
            sop=sop_result,
            confidence=conf_result,
            failures=failures,
        )

    def format_feedback(self, result: ResponseQualityResult) -> str:
        """将诊断质量检查结果格式化为 Agent 可理解的反馈文本。

        串联各检查器的 format_feedback() 输出，生成统一反馈。

        Args:
            result: 综合检查结果

        Returns:
            str: Markdown 格式反馈文本（全部通过时返回空字符串）
        """
        if result.passed or result.skipped:
            return ""

        sections: list[str] = []

        # 引入各检查器的 format_feedback
        from app.harness.hallucination import HallucinationGate
        from app.harness.evidence import EvidenceScorer
        from app.harness.sop import SopChecker
        from app.harness.confidence import ConfidenceAssessor

        feedback_generators = [
            ("hallucination", HallucinationGate.format_feedback, result.hallucination),
            ("evidence", EvidenceScorer.format_feedback, result.evidence),
            ("sop", SopChecker.format_feedback, result.sop),
            ("confidence", ConfidenceAssessor.format_feedback, result.confidence),
        ]

        for name, formatter, check_result in feedback_generators:
            if check_result is not None:
                fb = formatter(check_result)
                if fb:
                    sections.append(fb)

        if not sections:
            return ""

        # 添加总览
        header = (
            "[验证反馈 — 诊断质量] 以下诊断质量检查未通过，请修正回答：\n"
        )
        return header + "\n\n---\n\n".join(sections)
