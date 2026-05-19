"""MoveFileTool — safely move/rename a file."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import PathExists, PathNotExists, PathType, FileCreated, FileDeleted, tool_parameters
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p


@tool_parameters(properties={
    "source": p("string", "Absolute path to the source file. Must be a file, not a directory."),
    "dest": p("string", "Absolute path to the destination. If an existing directory, file is moved inside with original name."),
}, required=["source", "dest"])
class MoveFileTool(_FsTool):
    """Move or rename a file. Workspace-guarded, single-file only."""

    name = "move_file"
    description = (
        "**用途**: 移动或重命名单个文件。\n\n"
        "**什么时候用**:\n"
        "- 需要将文件移动到另一个目录时\n"
        "- 需要重命名文件时\n\n"
        "**什么时候不用**:\n"
        "- 需要复制文件 → 用 exec cp\n"
        "- 需要移动/重命名目录 → 用 exec mv\n"
        "- 需要修改文件内容 → 用 edit_file 或 write_file\n"
        "- 需要删除文件 → 用 delete_file\n"
    )

    _pre_validators = [PathExists("source"), PathType("source", "file"), PathNotExists("dest")]
    _post_validators = [FileCreated("dest"), FileDeleted("source")]

    async def execute(self, source: str = "", dest: str = "", **kwargs: Any) -> str:
        src_resolved = self._resolve(source)
        dst_resolved = self._resolve(dest)
        src_resolved.rename(dst_resolved)
        return f"Moved: {src_resolved.as_posix()} -> {dst_resolved.as_posix()}"
