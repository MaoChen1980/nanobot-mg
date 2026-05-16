from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema
from .filesystem_base import _FsTool

@tool_parameters(
    tool_parameters_schema(
        path=p("string", "The directory path to list"),
        recursive=p("boolean", "Recursively list all files (default false)"),
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
        "**限制**:\n"
        "- 默认最多 400 条\n"
        "- 自动跳过 .git / node_modules 等噪音目录\n\n"
        "**错误应对**:\n"
        "- 目录不存在 → 返回错误\n\n"
        "**边界条件**:\n"
        "- 需要按模式匹配文件名 → 用 glob\n"
        "- 需要搜索文件内容 → 用 grep\n\n"
        "**极简案例**: list_dir(path='src', recursive=True)\n"
        "→ 返回 src/ 下所有文件和目录的列表"
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
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

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

