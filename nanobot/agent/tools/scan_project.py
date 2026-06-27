"""Tool to scan a project directory and generate project_card.md for context injection."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


@tool_parameters(
    properties={
        "path": p("string", "Absolute path to the project directory to scan"),
    },
    required=["path"],
)
class ScanProjectTool(Tool):
    """Scan a project directory and load the project card into agent context."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop
    instruction = (
        "Scan overall project structure. Call this first when user asks you to look at or modify a project. "
        "For detailed file analysis use analyze."
    )

    name = "scan_project"

    @property
    def description(self) -> str:
        return (
            "Scan a project directory and generate a project card: "
            "language, build tools, test framework, and overall structure. "
            "Path must be the absolute project root."
        )

    async def execute(self, path: str, **kwargs: Any) -> str:
        from nanobot.agent.project_scanner import write_project_card

        project_path = Path(path).expanduser().resolve()
        if not project_path.is_dir():
            return f"Error: directory not found: {project_path.as_posix()}"

        try:
            write_project_card(project_path)
        except Exception as e:
            return f"Error scanning project: {e}"

        card_path = project_path / "project_card.md"
        card = card_path.read_text(encoding="utf-8").strip()

        return f"Project scanned: {project_path.as_posix()}\nProject card written to: {card_path.as_posix()}\n\n{card}"