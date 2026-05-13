"""MoveFileTool — safely move/rename a file."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import PathExists, PathNotExists, PathType, FileCreated, FileDeleted, tool_parameters
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p


@tool_parameters(properties={
    "source": p("string", "The source file path"),
    "dest": p("string", "The destination file path"),
}, required=["source", "dest"])
class MoveFileTool(_FsTool):
    """Move or rename a file. Workspace-guarded, single-file only."""

    name = "move_file"
    description = (
        "**用途**: 移动或重命名文件。\n\n"
        "**限制**:\n"
        "- 只支持单文件操作\n"
        "- 目标路径不可已存在\n\n"
        "**错误应对**:\n"
        "- 源文件不存在 → 返回错误\n"
        "- 目标已存在 → 返回错误（不会覆盖）\n\n"
        "**边界条件**:\n"
        "- 需要复制文件 → 用 exec cp / xcopy\n"
        "- 需要移动目录 → 用 exec mv\n\n"
        "**极简案例**: move_file(source='a.txt', dest='bak/a.txt')\n"
        "→ 移动文件，返回确认"
    )

    _pre_validators = [PathExists("source"), PathType("source", "file"), PathNotExists("dest")]
    _post_validators = [FileCreated("dest"), FileDeleted("source")]

    async def execute(self, source: str = "", dest: str = "", **kwargs: Any) -> str:
        src_resolved = self._resolve(source)
        dst_resolved = self._resolve(dest)
        src_resolved.rename(dst_resolved)
        return f"Moved: {source} -> {dest}"
