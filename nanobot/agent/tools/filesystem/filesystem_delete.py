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
    """Delete a single file. Absolute path only."""

    name = "delete_file"
    description = (
        "**Purpose**: Delete a single file.\n\n"
        "**When to use**:\n"
        "- When a file is no longer needed and should be deleted\n"
        "- When safer deletion than exec rm is desired (workspace-guarded with auto-verification)\n\n"
    )

    _pre_validators = [PathExists("path"), PathType("path", "file")]
    _post_validators = [FileDeleted("path")]

    async def execute(self, path: str = "", **kwargs: Any) -> str:
        resolved = self._resolve(path)
        resolved.unlink()
        return f"Deleted: {resolved.as_posix()}"
