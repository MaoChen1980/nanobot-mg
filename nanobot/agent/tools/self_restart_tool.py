"""self_restart_tool.py — Agent can call this to check and restart itself."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import build_parameters_schema

if TYPE_CHECKING:
    pass


@tool_parameters(build_parameters_schema())
class SelfRestartTool(Tool):
    """Check for code changes and restart nanobot if needed.

    Usage: call this tool after modifying nanobot code.
    It will:
    1. Check if there are uncommitted changes
    2. Run pip install -e . if changes exist
    3. Write a restart flag — gateway picks it up at next safe point

    No arguments required.
    """

    name = "self_restart"
    description = (
        "**用途**: 检查 nanobot 源码是否有变更，如有则安装并触发优雅重启。\n\n"
        "**什么时候用**:\n"
        "- 修改了 nanobot 代码后，不需要人类操作，自己完成重启\n\n"
        "**工作流程**: git diff → pip install -e . → 写重启 flag → gateway 自动重启\n\n"
        "**什么时候不用**:\n"
        "- 只改 workspace 文件不需要重启\n"
        "- 代码改错了会变砖，先想清楚再调用"
    )

    async def execute(self, **kwargs: Any) -> str:
        try:
            return await self._do_restart_check()
        except Exception as exc:
            logger.exception("SelfRestartTool.execute error")
            return f"Error during restart check: {exc}"

    def _check_git_diff(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd="E:/claude/nanobot",
                capture_output=True,
                text=True,
                timeout=10,
            )
            return bool(result.stdout.strip())
        except Exception as e:
            logger.warning("git diff check failed: {}", e)
            return False

    def _pip_install(self) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", "E:/claude/nanobot"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr[:200]
        except Exception as e:
            return False, str(e)
    async def _graceful_restart_via_shutdown(self, gateway_port: int = 18790) -> None:
        """Call /api/shutdown to trigger a clean gateway restart."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(f"http://localhost:{gateway_port}/api/shutdown")
                logger.info("self_restart: /api/shutdown → {}", resp.status_code)
        except Exception as e:
            logger.warning("self_restart: /api/shutdown failed ({}), flag file will handle restart", e)

    async def _do_restart_check(self) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        lines = [f"[{ts}] self_restart check"]

        has_changes = self._check_git_diff()
        lines.append(f"uncommitted changes: {has_changes}")

        if not has_changes:
            lines.append("no changes — nothing to do")
            return "\n".join(lines)

        lines.append("changes detected — running pip install -e .")
        ok, err = self._pip_install()
        if not ok:
            lines.append(f"pip install failed: {err}")
            lines.append("restart aborted — fix the error first")
            return "\n".join(lines)

        # Write flag as backup, then call /api/shutdown for immediate graceful restart
        flag_file = Path.home() / ".nanobot" / "workspace" / ".agent" / "_restart_flag.json"
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.write_text(json.dumps({"requested_at": ts}, ensure_ascii=False), encoding="utf-8")
        lines.append(f"flag: {flag_file.name}")

        await self._graceful_restart_via_shutdown()
        lines.append("/api/shutdown called — gateway will restart gracefully")
        return "\n".join(lines)