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

    name = "list_dir"

    description = (
        "**用途**: 列出目录内容，支持递归。\n\n"
        "**输出格式**:\n"
        "- 每行一个绝对路径\n"
        "- 目录路径以 / 结尾（便于区分文件和目录）\n"
        "- 递归模式输出所有子条目\n\n"
        "**什么时候用**:\n"
        "- 需要查看目录中有哪些文件和子目录时\n"
        "- 需要递归探索项目结构时\n\n"
        "**什么时候不用**:\n"
        "- 需要按模式匹配文件名 → 用 glob 或 file_search\n"
        "- 需要搜索文件内容 → 用 grep\n"
        "- 需要读取文件内容 → 用 read_file\n"
        "- 需要单个文件状态（是否存在/类型）→ 用 stat\n"
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
