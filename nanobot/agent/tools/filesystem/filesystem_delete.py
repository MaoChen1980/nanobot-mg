"""DeleteFileTool — safely delete a single file."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.agent.tools.base import PathExists, PathType, FileDeleted, tool_parameters
from nanobot.agent.tools.danger import danger_warning
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p


@tool_parameters(properties={
    "path": p("string", "Absolute path to a file to delete. Workspace-guarded, rejects system paths."),
    "danger_override": p("boolean",
        "When true, bypasses danger detection and allows deleting files. "
        "Use only after verifying the file is safe to remove. "
        "Default: false. Detection re-enables automatically for the next call.",
        default=False,
    ),
}, required=["path"])
class DeleteFileTool(_FsTool):
    """Delete a single file. Absolute path only."""
    instruction = "Delete files. Use with caution — action is irreversible. For modifying content use edit_file, not delete_file."

    name = "delete_file"
    description = (
        "Delete a single file. Workspace-guarded with auto-verification. "
        "Danger detection enabled by default — use danger_override=true to proceed."
    )

    _pre_validators = [PathExists("path"), PathType("path", "file")]
    _post_validators = [FileDeleted("path")]

    async def execute(self, path: str = "", danger_override: bool = False, **kwargs: Any) -> str:
        resolved = self._resolve(path)

        # Danger detection: warn before deleting files
        if not danger_override:
            try:
                size = resolved.stat().st_size
            except OSError:
                size = 0
            size_str = f" ({size} bytes)" if size > 0 else ""
            return danger_warning(
                problem=f"This will permanently delete {resolved.name}{size_str}",
                risk="Permanent data loss — deleted files cannot be recovered",
                suggestion="Back up the file first (e.g., git commit, save_checkpoint, or copy to a temp location) or confirm it's no longer needed before proceeding",
                tool_name="delete_file",
            )

        resolved.unlink()
        logger.info("Deleted file (danger_override): {}", resolved)
        return f"Deleted: {resolved.as_posix()}"