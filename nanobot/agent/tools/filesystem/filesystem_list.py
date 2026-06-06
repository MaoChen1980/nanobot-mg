from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from .filesystem_base import _FsTool

@tool_parameters(
    build_parameters_schema(
        path=p("string", "Absolute path to a directory to list."),
        recursive=p("boolean", "Recursively list all files (default false)", default=False),
        max_entries=p("integer", "Maximum entries to return (default 400)",
            minimum=1,
        ),
        required=["path"],
    )
)
class ListDirTool(_FsTool):
    """List directory contents with optional recursion."""

    _DEFAULT_MAX = 400
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    name = "list_dir_tool"

    description = (
        "**Purpose**: List directory contents. Supports recursive traversal.\n\n"
        "**Output Format**:\n"
        "- One absolute path per line\n"
        "- Directory paths end with / (to distinguish files from directories)\n"
        "- Recursive mode outputs all sub-entries\n\n"
        "**When to use**:\n"
        "- When exploring which files and subdirectories exist in a directory\n"
        "- When recursively exploring a project's structure\n\n"
    )

    read_only = True

    async def execute(
        self, path: str | None = None, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if path is None:
                raise ValueError("Unknown path")
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}"
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        items.append(f"{item.resolve().as_posix()}/" if item.is_dir() else item.resolve().as_posix())
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        items.append(f"{item.resolve().as_posix()}/" if item.is_dir() else item.resolve().as_posix())

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            logger.warning("ListDir permission denied: {}", e)
            return f"Error: {e}"
        except Exception as e:
            logger.warning("ListDir failed: {}", e)
            return f"Error listing directory: {e}"
