"""证据链评分 (Layer 3 — 反馈防线)

评估 Agent 诊断结论的证据链完整性：
1. 结论是否绑定了具体的数据来源？（文件名、工具输出、日志行）
2. 推理链条是否清晰？（数据 → 分析 → 根因 → 建议）
3. 是否存在无依据的断言？

与 hallucination.py 的区别：
- hallucination.py 检查"回答中的事实是否在知识库中存在"
- evidence.py 检查"结论是否引用了证据"（不管知识库，只看回答文本本身的结构）

核心原则：
- 纯文本分析，零 API 开销
- 不通过 → 生成反馈 → 退回 Agent 重写
- 只在诊断类场景触发（非诊断问答跳过）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class EvidenceCheckResult:
    """证据链检查结果

    Attributes:
        passed: 是否通过（所有维度都达标）
        has_source_citation: 是否引用了具体数据来源
        has_reasoning_chain: 是否有"数据→分析→结论"的推理链
        source_count: 检测到的引用来源数量
        reasoning_indicators: 检测到的推理标志词数量（如 "因此"、"根因是"）
        unsubstantiated: 无证据支撑的断言列表（供反馈用）
        score: 综合评分 (0.0 ~ 1.0)
        skipped: 是否跳过（非诊断场景）
    """
    passed: bool
    has_source_citation: bool = False
    has_reasoning_chain: bool = False
    source_count: int = 0
    reasoning_indicators: int = 0
    unsubstantiated: list[str] = field(default_factory=list)
    score: float = 0.0
    skipped: bool = False


class EvidenceScorer:
    """证据链评分器 —— 分析回答文本的结构完整性。

    使用方法:
        scorer = EvidenceScorer()
        result = scorer.evaluate(answer=agent_answer, is_diagnostic=True)

        if not result.passed:
            feedback = scorer.format_feedback(result)
    """

    # ── 阈值 ──────────────────────────────────────────────────────────────

    MIN_SOURCE_COUNT = 1            # 诊断回答至少引用 1 个数据来源
    MIN_REASONING_INDICATORS = 2    # 至少出现 2 个推理标志词
    PASS_SCORE_THRESHOLD = 0.5      # 综合评分 >= 0.5 通过

    # ── 来源引用模式 ──────────────────────────────────────────────────────

    # 判断回答是否引用了具体的数据来源
    SOURCE_PATTERNS: list[tuple[str, str]] = [
        # (类型, 正则)
        ("文件引用",    r"""(?:文件|文档|配置|代码)[：:]\s*[`\"]?[-\w./\\]+\.(?:py|ya?ml|json|toml|conf|md|log|txt|ini)[`\"]?"""),
        ("日志行",      r"""\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}[:.]\d{2}"""),
        ("指标值",      r"""(?:CPU|内存|磁盘|网络|连接数|QPS|TPS|延迟|吞吐)[：:]\s*\d+(?:\.\d+)?\s*(?:%|MB|GB|ms|个|次)?"""),
        ("工具输出",    r"""```[\s\S]*?```"""),  # 代码块包裹的工具输出
        ("告警信息",    r"""(?:告警|报警|alert|alarm)[：:\s]+[^\n。]{10,}"""),
        ("命令结果",    r"""(?:执行|运行|输出|返回)[：:]\s*`[^`]+`"""),
        ("数值数据",    r"""\b\d+\s*(?:%|ms|MB|GB|次|个|台|条|Mbps|Gbps)\b"""),
        ("路径引用",    r"""(?:/[\w./-]+){2,}"""),
    ]

    # ── 推理链标志词 ──────────────────────────────────────────────────────

    REASONING_INDICATORS: list[str] = [
        "因此", "所以", "由此", "可以判断", "根因是",
        "原因在于", "导致", "引起", "证明", "说明",
        "排查发现", "检查发现", "分析发现", "调查发现",
        "证据表明", "数据表明", "日志显示", "监控显示",
        "根据", "基于", "依据", "来源",
        "因为", "由于", "从而", "进而",
    ]

    # ── 减分项：无依据的断言模式 ──────────────────────────────────────────

    # 如果回答使用了这些绝对化表述但没有附带证据引用，标记为无依据断言
    UNSOUND_PATTERNS: list[str] = [
        r"肯定(?:是|因为|由)",
        r"绝对(?:是|因为|由)",
        r"(?:一定|必定|必然)(?:是|因为|由)",
        r"显然(?:是|因为|由)",
        r"毫无疑问",
        r"(?:正是|就是)这个原因",
    ]

    # ── 公开方法 ──────────────────────────────────────────────────────────

    def evaluate(self, answer: str, is_diagnostic: bool = False) -> EvidenceCheckResult:
        """评估回答的证据链完整性。

        Args:
            answer: Agent 生成的回答文本
            is_diagnostic: 是否为诊断类查询（False 时跳过）

        Returns:
            EvidenceCheckResult: 评估结果
        """
        if not is_diagnostic:
            logger.debug("[证据链] 非诊断场景，跳过")
            return EvidenceCheckResult(passed=True, skipped=True)

        if not answer or len(answer.strip()) < 50:
            logger.debug("[证据链] 回答过短，跳过")
            return EvidenceCheckResult(passed=True, skipped=True)

        # ① 检测来源引用
        sources_found: list[str] = []
        for src_type, pattern in self.SOURCE_PATTERNS:
            matches = re.findall(pattern, answer, re.IGNORECASE)
            if matches:
                sources_found.append(src_type)

        source_count = len(sources_found)
        has_source = source_count >= self.MIN_SOURCE_COUNT

        # ② 检测推理链
        reasoning_count = 0
        for indicator in self.REASONING_INDICATORS:
            # 使用 word boundary 匹配（中文词边界用前后缀算法）
            reasoning_count += len(re.findall(re.escape(indicator), answer))

        has_reasoning = reasoning_count >= self.MIN_REASONING_INDICATORS

        # ③ 检测无依据断言
        unsubstantiated: list[str] = []
        if source_count == 0:
            for pattern in self.UNSOUND_PATTERNS:
                matches = re.findall(pattern, answer, re.IGNORECASE)
                for m in matches:
                    unsubstantiated.append(f"绝对化断言「{m}」但无证据引用")

        # ④ 综合评分
        # 来源引用权重 0.5 + 推理链权重 0.3 + 无依据扣分 0.2
        source_score = min(source_count / max(self.MIN_SOURCE_COUNT, 1), 1.0) * 0.5
        reasoning_score = min(reasoning_count / max(self.MIN_REASONING_INDICATORS, 1), 1.0) * 0.3
        penalty = min(len(unsubstantiated) * 0.1, 0.2)
        score = max(source_score + reasoning_score - penalty, 0.0)

        passed = score >= self.PASS_SCORE_THRESHOLD

        if passed:
            logger.info(
                f"[证据链] 通过 — 评分 {score:.0%}, "
                f"来源={source_count}, 推理标志={reasoning_count}"
            )
        else:
            logger.warning(
                f"[证据链] 未通过 — 评分 {score:.0%}, "
                f"来源={source_count}, 推理标志={reasoning_count}, "
                f"无依据断言={len(unsubstantiated)}"
            )

        return EvidenceCheckResult(
            passed=passed,
            has_source_citation=has_source,
            has_reasoning_chain=has_reasoning,
            source_count=source_count,
            reasoning_indicators=reasoning_count,
            unsubstantiated=unsubstantiated,
            score=score,
        )

    # ── 反馈生成 ──────────────────────────────────────────────────────────

    @staticmethod
    def format_feedback(result: EvidenceCheckResult) -> str:
        """将证据链评估结果格式化为 Agent 可理解的反馈文本。"""
        if result.passed or result.skipped:
            return ""

        lines = ["[验证反馈 — 证据链] 诊断回答的证据链不够完整：", ""]

        if not result.has_source_citation:
            lines.append(
                "- **缺少数据来源引用**：请标注结论对应的日志行、指标值、"
                "配置文件或工具输出"
            )
        if not result.has_reasoning_chain:
            lines.append(
                "- **推理链不清晰**：请使用「根据...→分析发现...→因此根因是...」"
                "的结构呈现推理过程"
            )
        if result.unsubstantiated:
            lines.append("- **以下断言缺少证据支撑**：")
            for item in result.unsubstantiated:
                lines.append(f"  - {item}")

        lines.extend([
            "",
            f"综合评分: {result.score:.0%}（阈值: {EvidenceScorer.PASS_SCORE_THRESHOLD:.0%}）",
            "请补充数据来源引用和清晰的推理链后重新回答。",
        ])
        return "\n".join(lines)


# 模块级单例
evidence_scorer = EvidenceScorer()
