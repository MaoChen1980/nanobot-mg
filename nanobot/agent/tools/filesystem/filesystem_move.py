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
        "**Purpose**: Move or rename a single file.\n\n"
        "**When to use**:\n"
        "- When moving a file to another directory\n"
        "- When renaming a file\n\n"
    )

    _pre_validators = [PathExists("source"), PathType("source", "file"), PathNotExists("dest")]
    _post_validators = [FileCreated("dest"), FileDeleted("source")]

    async def execute(self, source: str = "", dest: str = "", **kwargs: Any) -> str:
        src_resolved = self._resolve(source)
        dst_resolved = self._resolve(dest)
        src_resolved.rename(dst_resolved)
        return f"Moved: {src_resolved.as_posix()} -> {dst_resolved.as_posix()}"
