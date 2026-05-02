"""Subagent tool registration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.agent.tools.filesystem import ListDirTool, ReadFileTool, WriteFileTool, EditFileTool
from nanobot.agent.tools.search import GlobTool, GrepTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.skills import BUILTIN_SKILLS_DIR

if TYPE_CHECKING:
    from nanobot.config.schema import ExecToolConfig, WebToolsConfig


def build_subagent_tools(
    workspace: Path,
    web_config: WebToolsConfig,
    exec_config: ExecToolConfig,
    restrict_to_workspace: bool,
) -> ToolRegistry:
    """Build a ToolRegistry for subagent execution (read + write, no spawn)."""
    tools = ToolRegistry()
    allowed_dir = workspace if restrict_to_workspace else None
    extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
    tools.register(ReadFileTool(workspace=workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
    tools.register(ListDirTool(workspace=workspace, allowed_dir=allowed_dir))
    tools.register(GlobTool(workspace=workspace, allowed_dir=allowed_dir))
    tools.register(GrepTool(workspace=workspace, allowed_dir=allowed_dir))
    for cls in (WriteFileTool, EditFileTool):
        tools.register(cls(workspace=workspace, allowed_dir=allowed_dir))
    if web_config.enable:
        tools.register(WebSearchTool(config=web_config.search, proxy=web_config.proxy, user_agent=web_config.user_agent))
        tools.register(WebFetchTool(config=web_config.fetch, proxy=web_config.proxy, user_agent=web_config.user_agent))
    if exec_config.enable:
        tools.register(
            ExecTool(
                working_dir=str(workspace),
                timeout=exec_config.timeout,
                restrict_to_workspace=restrict_to_workspace,
                sandbox=exec_config.sandbox,
                path_append=exec_config.path_append,
                allowed_env_keys=exec_config.allowed_env_keys,
            )
        )
    return tools