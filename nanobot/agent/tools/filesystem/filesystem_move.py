"""MoveFileTool — safely move/rename a file."""

from __future__ import annotations

import shutil
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import PathExists, PathType, FileCreated, FileDeleted, tool_parameters
from nanobot.agent.tools.danger import danger_warning
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p


@tool_parameters(properties={
    "source": p("string", "Absolute path to the source file. Must be a file, not a directory."),
    "dest": p("string", "Absolute path to the destination. If an existing directory, file is moved inside with original name."),
    "danger_override": p("boolean",
        "When true, bypasses danger detection and allows overwriting existing destination files. "
        "Use only after verifying the destination is safe to overwrite. "
        "Default: false. Detection re-enables automatically for the next call.",
        default=False,
    ),
}, required=["source", "dest"])
class MoveFileTool(_FsTool):
    """Move or rename a file. Workspace-guarded, single-file only."""

    name = "move_file_tool"
    description = (
        "**Purpose**: Move or rename a single file.\n\n"
        "**When to use**:\n"
        "- When moving a file to another directory\n"
        "- When renaming a file\n\n"
        "**Danger detection**: Enabled by default. Overwriting an existing file triggers a warning. "
        "Use danger_override=true to proceed when you are sure.\n"
    )

    _pre_validators = [PathExists("source"), PathType("source", "file")]
    _post_validators = [FileCreated("dest"), FileDeleted("source")]

    async def execute(self, source: str = "", dest: str = "", danger_override: bool = False, **kwargs: Any) -> str:
        src_resolved = self._resolve(source)
        dst_resolved = self._resolve(dest)

        # Danger detection: overwriting existing destination
        if not danger_override and dst_resolved.exists():
            try:
                size = dst_resolved.stat().st_size
            except OSError:
                size = 0
            size_str = f" ({size} bytes)" if size > 0 else ""
            return danger_warning(
                problem=f"Destination already exists: {dst_resolved.name}{size_str}",
                risk="Moving will overwrite the existing destination file — potential data loss",
                suggestion="Back up the destination file first (git commit or save_checkpoint), read its contents to verify, or choose a different destination path",
                tool_name="move_file_tool",
            )

        shutil.move(str(src_resolved), str(dst_resolved))
        logger.info("Moved {} -> {} (danger_override={})", src_resolved, dst_resolved, danger_override)
        return f"Moved: {src_resolved.as_posix()} -> {dst_resolved.as_posix()}"
