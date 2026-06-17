"""Subagent tool registration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nanobot.agent.tools.filesystem import (
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    DeleteFileTool,
    MoveFileTool,
)
from nanobot.agent.tools.search import GlobTool, GrepTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.explore_module import ExploreModuleTool
from nanobot.agent.tools.stage import SaveStageTool, ShowStagesTool, RestoreStageTool
from nanobot.agent.tools.analyze_tool import AnalyzeTool
from nanobot.agent.tools.reframe import ReframeTool
from nanobot.agent.tools.debug_root_cause import DebugRootCauseTool
from nanobot.agent.tools.assess_me_tool import AssessMeTool
from nanobot.agent.tools.semantic_search import SearchTextTool
from nanobot.agent.tools.memory_search import MemorySearchTool
from nanobot.agent.tools.conversation_search import ConversationSearchTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.skills import BUILTIN_SKILLS_DIR

if TYPE_CHECKING:
    from nanobot.agent.memory import MemoryStore
    from nanobot.config.schema import ExecToolConfig, WebToolsConfig


def build_subagent_tools(
    workspace: Path,
    web_config: WebToolsConfig,
    exec_config: ExecToolConfig,
    restrict_to_workspace: bool,
    memory_store: MemoryStore | None = None,
) -> ToolRegistry:
    """Build a ToolRegistry for subagent execution (read + write, no spawn)."""
    tools = ToolRegistry()
    allowed_dir = workspace if restrict_to_workspace else None
    extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None

    # --- core filesystem & search ---
    tools.register(ReadFileTool(workspace=workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
    tools.register(GlobTool(workspace=workspace, allowed_dir=allowed_dir))
    tools.register(GrepTool(workspace=workspace, allowed_dir=allowed_dir))
    for cls in (WriteFileTool, EditFileTool, DeleteFileTool, MoveFileTool):
        tools.register(cls(workspace=workspace, allowed_dir=allowed_dir))

    # --- batch read & analysis (read-only) ---
    tools.register(ExploreModuleTool(workspace=workspace, allowed_dir=allowed_dir))
    tools.register(SaveStageTool())
    tools.register(ShowStagesTool())
    tools.register(RestoreStageTool())
    tools.register(AnalyzeTool(workspace=workspace, allowed_dir=allowed_dir))
    tools.register(ReframeTool(workspace=workspace))
    tools.register(DebugRootCauseTool())
    tools.register(AssessMeTool())
    tools.register(SearchTextTool(workspace=workspace, allowed_dir=allowed_dir))

    # --- memory / framework / conversation search ---
    if memory_store is not None:
        tools.register(MemorySearchTool(store=memory_store))
        tools.register(ConversationSearchTool(store=memory_store))

    # --- web ---
    if web_config.enable:
        tools.register(
            WebSearchTool(config=web_config.search, proxy=web_config.proxy, user_agent=web_config.user_agent)
        )
        tools.register(
            WebFetchTool(config=web_config.fetch, proxy=web_config.proxy, user_agent=web_config.user_agent)
        )

    # --- shell ---
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
