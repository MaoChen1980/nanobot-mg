"""DeleteFileTool — safely delete a single file."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import PathExists, PathType, FileDeleted, tool_parameters
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p


@tool_parameters(properties={
    "path": p("string", "Absolute path to a file to delete. Workspace-guarded, rejects system paths."),
}, required=["path"])
class DeleteFileTool(_FsTool):
    """Delete a single file. Supports workspace-relative and absolute paths."""

    name = "delete_file"
    description = (
        "**用途**: 删除单个文件。\n\n"
        "**什么时候用**:\n"
        "- 需要删除不再需要的文件时\n"
        "- 需要比 exec rm 更安全的删除（带 workspace 保护和自动验证）时\n\n"
        "**什么时候不用**:\n"
        "- 需要删除目录 → 用 exec rmdir / rm -rf\n"
        "- 需要批量删除文件 → 用 exec rm\n"
        "- 需要移动/重命名文件 → 用 move_file\n"
    )

    _pre_validators = [PathExists("path"), PathType("path", "file")]
    _post_validators = [FileDeleted("path")]

    async def execute(self, path: str = "", **kwargs: Any) -> str:
        resolved = self._resolve(path)
        resolved.unlink()
        return f"Deleted: {resolved.as_posix()}"
