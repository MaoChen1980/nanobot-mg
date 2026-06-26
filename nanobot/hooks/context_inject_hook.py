"""
ContextInjectHook: inject project context before each run.

Phase 0 of the self-evolution feedback loop. Reads workspace metadata and
injects a project context block into the agent's prompt so subsequent reasoning
has the correct project scope from the start.

This prevents the "wrong project" failure mode where findings from one project
(e.g., trading backtests) are analyzed as if they belong to another (e.g.,
Android porting).
"""
from __future__ import annotations

from pathlib import Path

from nanobot.agent.hook import AgentHook, AgentHookContext


class ContextInjectHook(AgentHook):
    """Inject project-type context before run so agents know what they're working on."""

    __slots__ = ("_workspace", "_project_type", "_project_name")

    def __init__(self) -> None:
        super().__init__()
        self._workspace: Path | None = None
        self._project_type: str | None = None
        self._project_name: str | None = None

    def set_workspace(self, workspace: Path) -> None:
        """Called by AgentLoop to provide the workspace path."""
        self._workspace = workspace
        self._detect_project()

    def _detect_project(self) -> None:
        """Detect project type from workspace structure."""
        if self._workspace is None:
            return

        # Android/Kotlin project indicators
        if (self._workspace / "app" / "build.gradle.kts").exists():
            self._project_type = "android"
            self._project_name = "mobile-ai-agent"
            return

        # Python nanobot-mg framework
        if (self._workspace / "nanobot" / "hooks").exists():
            self._project_type = "python"
            self._project_name = "nanobot-mg"
            return

        # Trading/backtest projects
        if (self._workspace / "workspace").exists() or (self._workspace.parent / "workspace").exists():
            # Check if there's a trading subdirectory
            workspace_path = self._workspace / "workspace"
            if workspace_path.exists():
                for child in workspace_path.iterdir():
                    if child.is_dir() and any(
                        (child / f).exists()
                        for f in ["strategy.py", "backtest.py", "v9", "t_based"]
                    ):
                        self._project_type = "trading"
                        self._project_name = child.name
                        return

            # Legacy: check parent workspace
            parent_workspace = self._workspace.parent / "workspace"
            if parent_workspace.exists():
                self._project_type = "trading"
                self._project_name = self._workspace.name
                return

        self._project_type = "unknown"
        self._project_name = self._workspace.name

    async def before_run(self, context: AgentHookContext) -> None:
        """Inject project context into agent context."""
        if self._workspace is None:
            return

        # Build project context block
        project_info = {
            "project_type": self._project_type or "unknown",
            "project_name": self._project_name or self._workspace.name,
            "workspace": str(self._workspace.resolve()),
        }

        # Inject into agent's instruction context
        # This will be picked up by the prompt template
        context.metadata["project_context"] = project_info
        context.metadata["project_context_injected"] = True

    @property
    def project_type(self) -> str | None:
        return self._project_type

    @property
    def project_name(self) -> str | None:
        return self._project_name
