"""幻觉检测门 (Layer 1 — 知识防线)

Agent 回答后，检查其中的事实性主张是否能在知识库检索结果中找到原文依据。
找不到 → 标记为"推测"，降低可信度，必要时触发修正。

核心原则（来自四层防线设计）：
- Agent 不能自评 —— 验证逻辑是外部脚本，Agent 无法绕过
- 先闸门后 LLM —— 便宜、确定性的检测先跑，不通过就退回重做
- 优雅降级 —— 无检索结果时跳过（非故障），不阻塞正常对话
- 全部可观测 —— 每个决策（通过/标记/跳过）都有日志

设计：
- 纯启发式算法：正则提取 + 字符串包含匹配
- 零 API 调用，零 subprocess 开销
- 只在有检索结果时运行（无检索 → 跳过）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain_core.documents import Document
from loguru import logger


@dataclass
class HallucinationCheckResult:
    """幻觉检测结果

    Attributes:
        passed: 是否通过（可信度 >= 阈值）
        verified_claims: 能在知识库找到依据的主张数量
        unverified_claims: 无法验证的主张列表（供反馈用）
        total_claims: 提取到的事实主张总数
        score: 可信度评分 (0.0 ~ 1.0)，verified / total
        skipped: 是否因无检索结果而跳过
    """
    passed: bool
    verified_claims: int = 0
    unverified_claims: list[str] = field(default_factory=list)
    total_claims: int = 0
    score: float = 0.0
    skipped: bool = False


class HallucinationGate:
    """幻觉检测门 —— 回答 vs 知识库 的交叉验证。

    使用方法:
        gate = HallucinationGate()
        result = gate.check(answer=agent_answer, documents=retrieved_docs)

        if not result.passed:
            # result.unverified_claims 中包含无法验证的主张
            # 可以用这些信息生成反馈消息
    """

    # ── 阈值 ──────────────────────────────────────────────────────────────

    MIN_VERIFIABLE_RATIO = 0.3      # 至少 30% 的主张能在知识库找到依据
    MIN_CLAIMS_TO_CHECK = 3         # 少于 3 个主张时不做太严格的判断

    # ── 事实主张提取正则 ──────────────────────────────────────────────────

    # 以下模式用于从回答文本中提取"可验证的事实主张"
    # 主张 = 能被 grep 风格匹配的确定信息
    CLAIM_PATTERNS: list[tuple[str, str]] = [
        # (名称, 正则模式)
        ("数值+单位",   r"\b\d+(?:\.\d+)?\s*(?:%|ms|MB|GB|s|分钟|小时|次|个|台|条|Mbps|Gbps|MHz|GHz|core)\b"),
        ("文件路径",    r"""(?:/[-\w./]+|app/[-\w./]+\.(?:py|ya?ml|json|toml|conf|ini|cfg))"""),
        ("IP地址",     r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?\b"),
        ("命令/工具名", r"\b(?:kubectl|docker|systemctl|top|ps|netstat|curl|wget|grep|awk|sed|jq|helm|terraform|ansible|prometheus|grafana|nginx|redis|mysql|postgres|milvus)\b[\w-]*"),
        ("错误信息",    r"""(?:error|Error|ERROR|exception|Exception|failed|Failed|timeout|Timeout|refused|denied|killed|OOM)[:\s\w]*"""),
        ("端口号",     r"\b端口\s*[:：]?\s*\d{2,5}\b|\bport\s*[:：]?\s*\d{2,5}\b"),
        ("版本号",     r"\bv?\d+\.\d+(?:\.\d+)?(?:-[.\w]+)?\b"),
        ("配置项",     r"\b[A-Z_]{3,}(?:\s*[=:]\s*\S+)?"),
        ("K8s资源",    r"\b(?:pod|deployment|service|ingress|configmap|secret|namespace|node|daemonset|statefulset)[/\w-]*\b"),
    ]

    # ── 公开方法 ──────────────────────────────────────────────────────────

    def check(
        self,
        answer: str,
        documents: list[Document] | None = None,
    ) -> HallucinationCheckResult:
        """执行幻觉检测。

        Args:
            answer: Agent 生成的回答文本
            documents: 知识库检索结果（无则跳过）

        Returns:
            HallucinationCheckResult: 检测结果
        """
        # 无检索结果 → 跳过（非故障，Agent 可能在用通用知识回答）
        if not documents:
            logger.debug("[幻觉检测] 无检索结果，跳过")
            return HallucinationCheckResult(passed=True, skipped=True)

        # ① 从回答中提取事实主张
        claims = self._extract_claims(answer)
        if not claims:
            logger.debug("[幻觉检测] 未提取到事实主张，跳过")
            return HallucinationCheckResult(passed=True, total_claims=0, skipped=True)

        # ② 将检索文档合并为一个全文（用于快速匹配）
        doc_texts: list[str] = []
        for doc in documents:
            content = doc.page_content if hasattr(doc, "page_content") else str(doc)
            doc_texts.append(content)
        knowledge_base: str = "\n".join(doc_texts)

        # ③ 逐条验证
        verified: list[str] = []
        unverified: list[str] = []
        for claim in claims:
            if self._claim_in_text(claim, knowledge_base):
                verified.append(claim)
            else:
                unverified.append(claim)

        total = len(claims)
        score = len(verified) / total if total > 0 else 1.0

        # ④ 判定
        passed = score >= self.MIN_VERIFIABLE_RATIO
        if total < self.MIN_CLAIMS_TO_CHECK:
            # 主张太少，降低要求：有一个能验证就过
            passed = len(verified) >= 1 or len(claims) < 2

        # ⑤ 日志
        if passed:
            logger.info(
                f"[幻觉检测] 通过 — {len(verified)}/{total} 主张可验证 "
                f"(可信度: {score:.0%})"
            )
        else:
            logger.warning(
                f"[幻觉检测] 未通过 — 仅 {len(verified)}/{total} 主张可验证 "
                f"(可信度: {score:.0%}), {len(unverified)} 条无法验证: "
                f"{unverified[:3]}"  # 只日志前 3 条
            )

        return HallucinationCheckResult(
            passed=passed,
            verified_claims=len(verified),
            unverified_claims=unverified,
            total_claims=total,
            score=score,
        )

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _extract_claims(self, text: str) -> list[str]:
        """从回答文本中提取事实主张（去重）。"""
        claims: list[str] = []
        seen: set[str] = set()

        for name, pattern in self.CLAIM_PATTERNS:
            for match in re.finditer(pattern, text):
                claim = match.group(0).strip()
                if claim and claim not in seen and len(claim) >= 2:
                    seen.add(claim)
                    claims.append(claim)

        logger.debug(
            f"[幻觉检测] 从回答中提取到 {len(claims)} 条事实主张"
        )
        return claims

    @staticmethod
    def _claim_in_text(claim: str, knowledge_text: str) -> bool:
        """检查单条主张是否在知识库文本中出现。

        使用大小写不敏感的包含匹配。
        对于过短的主张（<4 字符），使用词边界匹配避免误报。
        """
        if len(claim) < 4:
            # 短主张用词边界匹配（避免 "ms" 匹配到 "msec" 之类的误报）
            pattern = re.escape(claim)
            return bool(re.search(rf"\b{pattern}\b", knowledge_text, re.IGNORECASE))
        return claim.lower() in knowledge_text.lower()

    # ── 反馈生成 ──────────────────────────────────────────────────────────

    @staticmethod
    def format_feedback(result: HallucinationCheckResult) -> str:
        """将幻觉检测结果格式化为 Agent 可理解的反馈文本。

        Args:
            result: 检测结果

        Returns:
            str: Markdown 格式反馈文本（结果为 passed 时返回空字符串）
        """
        if result.passed or result.skipped or not result.unverified_claims:
            return ""

        lines = [
            "[验证反馈 — 知识防线] 以下主张在知识库中未找到依据，可能存在幻觉：",
            "",
        ]
        for i, claim in enumerate(result.unverified_claims[:10], 1):
            lines.append(f"{i}. `{claim}`")

        if len(result.unverified_claims) > 10:
            lines.append(f"... 共 {len(result.unverified_claims)} 条")

        lines.extend([
            "",
            f"可信度: {result.score:.0%} ({result.verified_claims}/{result.total_claims})",
            "请重新审查以上主张，如果无法在知识库找到依据，请明确标注为「推测」或修正回答。",
        ])
        return "\n".join(lines)


# 模块级单例
hallucination_gate = HallucinationGate()
