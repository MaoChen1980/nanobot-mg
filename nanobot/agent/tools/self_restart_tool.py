"""self_restart_tool.py — Agent can call this to restart nanobot."""

from __future__ import annotations

import json
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
    """Restart nanobot framework."""

    name = "self_restart_tool"
    description = "Request a framework restart (gateway performs the actual restart on next iteration). No parameters."

    async def execute(self, **kwargs: Any) -> str:
        try:
            errors = await self._check_modified_code()
            if errors:
                return "Restart blocked — code check failed:\n" + errors
            return await self._do_restart()
        except Exception as exc:
            logger.exception("SelfRestartTool.execute error")
            return f"Error during restart: {exc}"

    async def _check_modified_code(self) -> str | None:
        """Check .py files in nanobot package before restart.

        Scans all .py files under the nanobot package directory and compiles
        each one. Files shipped with the package are already valid — only
        self-modified code would fail. Works for both git and pip installs.
        Returns error message or None.
        """
        import nanobot

        pkg_root = Path(nanobot.__file__).resolve().parent
        errors: list[str] = []
        for py_path in sorted(pkg_root.rglob("*.py")):
            try:
                compile(py_path.read_text(encoding="utf-8"), py_path.name, "exec")
            except SyntaxError as exc:
                rel = py_path.relative_to(pkg_root.parent)
                errors.append(f"  {rel}: {exc}")

        if errors:
            return "\n".join(errors)
        return None

    async def _do_restart(self) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        flag_file = Path.home() / ".nanobot" / "workspace" / "_restart_flag.json"
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.write_text(json.dumps({"requested_at": ts}, ensure_ascii=False), encoding="utf-8")
        logger.info("self_restart: restart flag written, gateway will restart on next iteration")
        return "Restart requested."