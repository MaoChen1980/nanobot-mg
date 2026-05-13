"""DeleteFileTool — safely delete a single file."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import PathExists, PathType, FileDeleted, tool_parameters
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p


@tool_parameters(properties={
    "path": p("string", "The file path to delete"),
}, required=["path"])
class DeleteFileTool(_FsTool):
    """Delete a single file. Supports workspace-relative and absolute paths."""

    name = "delete_file"
    description = (
        "Delete a file.\n\n"
        "Use this when:\n"
        "- You need to remove a file that is no longer needed\n"
        "- You need to clean up temporary or generated files\n\n"
        "Do NOT use when:\n"
        "- You need to delete a directory — use exec rm -rf / rmdir instead\n"
        "- You want to move a file to trash — no undo available\n\n"
        "Safer than exec rm — workspace-guarded and single-file only. "
        "Framework auto-verifies: path exists, path is a file (not directory). "
        "Auto-confirms deletion after execution."
    )

    _pre_validators = [PathExists("path"), PathType("path", "file")]
    _post_validators = [FileDeleted("path")]

    async def execute(self, path: str = "", **kwargs: Any) -> str:
        resolved = self._resolve(path)
        resolved.unlink()
        return f"Deleted: {resolved}"
