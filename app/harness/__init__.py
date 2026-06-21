"""IOAM Harness — 四层防线系统

Agent 的"操作系统级"控制层，治理 Agent 整个生命周期的可靠性。

四层防线架构：
  Layer 1 (知识防线): 幻觉检测 — 回答中的事实主张能否在知识库找到依据？
  Layer 2 (约束防线): 权限闸门 — 工具分级（读/建议/执行）、输出有效性校验
  Layer 3 (反馈防线): 质量门禁 — 代码质量 + 证据链 + SOP 合规 + 置信度
  Layer 4 (熵控防线): 熔断监控 — 连续失败阻断、上下文超限预警、成本追踪

已实现:
  Layer 1: HallucinationGate      — 幻觉检测门
  Layer 3: Validator              — 代码质量门禁 (ruff/mypy/pytest)
           FeedbackLoop            — 验证反馈循环 (代码)
           EvidenceScorer          — 证据链评分
           SopChecker              — SOP 合规检查
           ConfidenceAssessor      — 诊断置信度评估

待实现:
  Layer 2: 工具分级闸门、输出有效性校验
  Layer 4: 熔断器、Token/成本追踪、会话清理

Harness 是独立模块 — 不耦合到 RagAgentService 内部，可单独测试、单独演进。
"""

# ── Layer 1: 知识防线 ─────────────────────────────────────────────────────
from app.harness.hallucination import (
    HallucinationGate,
    HallucinationCheckResult,
    hallucination_gate,
)

# ── Layer 3: 反馈防线 — 代码质量 ──────────────────────────────────────────
from app.harness.validator import Validator
from app.harness.feedback import (
    FeedbackStatus,
    Feedback,
    FeedbackResult,
    FeedbackLoop,
)

# ── Layer 3: 反馈防线 — 诊断质量 ──────────────────────────────────────────
from app.harness.evidence import (
    EvidenceScorer,
    EvidenceCheckResult,
    evidence_scorer,
)
from app.harness.sop import (
    SopChecker,
    SopCheckResult,
    sop_checker,
)
from app.harness.confidence import (
    ConfidenceAssessor,
    ConfidenceResult,
    confidence_assessor,
)

# ── 长期记忆管理 ──────────────────────────────────────────────────────────
from app.harness.memory import MemoryManager

__all__ = [
    # Layer 1 — 知识防线
    "HallucinationGate",
    "HallucinationCheckResult",
    "hallucination_gate",
    # Layer 3 — 代码质量
    "FeedbackStatus",
    "Feedback",
    "FeedbackResult",
    "FeedbackLoop",
    "Validator",
    # Layer 3 — 诊断质量
    "EvidenceScorer",
    "EvidenceCheckResult",
    "evidence_scorer",
    "SopChecker",
    "SopCheckResult",
    "sop_checker",
    "ConfidenceAssessor",
    "ConfidenceResult",
    "confidence_assessor",
    # 长期记忆
    "MemoryManager",
]
