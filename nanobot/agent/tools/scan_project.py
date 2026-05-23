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

    name = "scan_project"

    @property
    def description(self) -> str:
        return (
            "**用途**: 扫描项目目录并生成项目卡片，让 agent 理解项目结构。\n\n"
            "**什么时候用**:\n"
            "- 用户让你帮忙看/改某个项目时，先调这个工具扫描项目\n"
            "- 想了解项目结构、语言、构建工具、测试框架时\n\n"
            "**什么时候不用**:\n"
            "- 只需要读单个文件内容 → 用 read_file\n\n"
            "**注意事项**:\n"
            "- path 必须是项目根目录的绝对路径"
        )

    async def execute(self, path: str, **kwargs: Any) -> str:
        from nanobot.agent.project_scanner import write_project_card

        project_path = Path(path).expanduser().resolve()
        if not project_path.is_dir():
            return f"Error: directory not found: {project_path}"

        try:
            write_project_card(project_path)
        except Exception as e:
            return f"Error scanning project: {e}"

        card_path = project_path / "project_card.md"
        card = card_path.read_text(encoding="utf-8").strip()

        return f"Project scanned: {project_path}\nProject card written to: {card_path}\n\n{card}"
