"""质量门禁运行器

封装 ruff/mypy/pytest 的 subprocess 调用。
不做策略判断（判断由 FeedbackLoop 负责）。
所有 subprocess 调用通过 asyncio.to_thread 异步化，不阻塞 event loop。
"""

import asyncio
import subprocess
import sys
from typing import Optional

from loguru import logger

from app.harness.feedback import Feedback, FeedbackStatus


# subprocess 超时（秒）
_SUBPROCESS_TIMEOUT = 120


class Validator:
    """运行 lint → typecheck → test，返回结构化结果。

    所有检查按顺序执行。每项检查独立捕获异常，
    单项失败不影响后续检查。

    工具未安装时优雅降级，返回友好提示而非崩溃。
    """

    LINT_CMD = [sys.executable, "-m", "ruff", "check", "app/"]
    TYPECHECK_CMD = [sys.executable, "-m", "mypy", "app/"]
    # 使用 -o addopts="" 覆盖 pyproject.toml 中可能冲突的 addopts（如 --cov）
    TEST_CMD = [
        sys.executable, "-m", "pytest", "tests/",
        "-q", "--tb=short", "-o", "addopts=",
    ]

    async def run_all(self) -> list[Feedback]:
        """顺序运行 lint → typecheck → test，返回所有结果。

        Returns:
            list[Feedback]: 三项检查的结果（顺序: ruff, mypy, pytest）
        """
        results: list[Feedback] = []
        for cmd, name in [
            (self.LINT_CMD, "ruff"),
            (self.TYPECHECK_CMD, "mypy"),
            (self.TEST_CMD, "pytest"),
        ]:
            results.append(await self._run(cmd, name))
        return results

    async def _run(self, cmd: list[str], name: str) -> Feedback:
        """运行单个命令并返回结构化结果。

        通过 asyncio.to_thread 在后台线程池执行 subprocess，
        避免阻塞 FastAPI event loop。

        Args:
            cmd: 命令及其参数列表
            name: 检查步骤名称（用于日志和 Feedback.step）

        Returns:
            Feedback: 结构化检查结果
        """
        logger.debug(f"[Validator] 开始 {name} 检查...")

        try:
            returncode, stdout, stderr = await asyncio.to_thread(
                _run_subprocess, cmd, _SUBPROCESS_TIMEOUT
            )

            if returncode == 0:
                logger.debug(f"[Validator] {name}: PASS")
                return Feedback(FeedbackStatus.PASS, name, "通过")

            # 截取最后 15 行错误（通常最关键）
            output = stdout or stderr

            # 检测工具未安装（python -m X → "No module named X"）
            if "No module named" in output and name in output:
                logger.warning(f"[Validator] {name}: 工具未安装")
                return Feedback(
                    FeedbackStatus.FAIL,
                    name,
                    "工具未安装",
                    f"请在虚拟环境中安装: pip install {name}",
                )

            error_lines = output.strip().split("\n") if output.strip() else []
            short = "\n".join(error_lines[-15:]) if error_lines else "(无输出)"

            logger.debug(f"[Validator] {name}: FAIL (exit={returncode})")
            return Feedback(
                FeedbackStatus.FAIL,
                name,
                f"exit={returncode}",
                fix_hint=short,
            )

        except FileNotFoundError:
            logger.warning(f"[Validator] {name}: 命令不可用 ({cmd[0]} {cmd[2]})")
            return Feedback(
                FeedbackStatus.FAIL,
                name,
                "命令不可用",
                f"请确认已安装: pip install {name}",
            )


def _run_subprocess(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """同步 subprocess 调用（在 asyncio.to_thread 中运行）。

    提取为独立函数以便 asyncio.to_thread 直接调用。
    """
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr
