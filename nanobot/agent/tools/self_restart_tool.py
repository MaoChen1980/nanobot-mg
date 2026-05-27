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

    name = "self_restart"
    description = "LLM 重新启动 nanobot 框架自身所用。无参数。"

    async def execute(self, **kwargs: Any) -> str:
        try:
            return await self._do_restart()
        except Exception as exc:
            logger.exception("SelfRestartTool.execute error")
            return f"Error during restart: {exc}"

    async def _do_restart(self) -> str:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        flag_file = Path.home() / ".nanobot" / "workspace" / "_restart_flag.json"
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.write_text(json.dumps({"requested_at": ts}, ensure_ascii=False), encoding="utf-8")
        logger.info("self_restart: restart flag written, gateway will restart on next iteration")
        return "Restart requested."