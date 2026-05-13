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
        "Move or rename a file.\n\n"
        "Use this when:\n"
        "- You need to rename a file\n"
        "- You need to move a file to a different directory\n\n"
        "Do NOT use when:\n"
        "- You need to copy a file — use exec cp/xcopy instead\n"
        "- You need to move directories — file operations only\n\n"
        "Safer than exec mv — workspace-guarded and single-file only. "
        "Framework auto-verifies: source exists, source is a file, destination does not exist. "
        "Auto-confirms both files after execution."
    )

    _pre_validators = [PathExists("source"), PathType("source", "file"), PathNotExists("dest")]
    _post_validators = [FileCreated("dest"), FileDeleted("source")]

    async def execute(self, source: str = "", dest: str = "", **kwargs: Any) -> str:
        src_resolved = self._resolve(source)
        dst_resolved = self._resolve(dest)
        src_resolved.rename(dst_resolved)
        return f"Moved: {source} -> {dest}"
