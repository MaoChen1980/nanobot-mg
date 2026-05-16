"""DeleteFileTool — safely delete a single file."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import PathExists, PathType, FileDeleted, tool_parameters
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p


@tool_parameters(properties={
    "path": p("string", "File path to delete — workspace-guarded, rejects system paths."),
}, required=["path"])
class DeleteFileTool(_FsTool):
    """Delete a single file. Supports workspace-relative and absolute paths."""

    name = "delete_file"
    description = (
        "**用途**: 删除单个文件。\n\n"
        "**限制**:\n"
        "- 只能删除文件，不能删除目录\n"
        "- 不可撤销（没有回收站）\n\n"
        "**错误应对**:\n"
        "- 文件不存在 → 返回错误\n"
        "- 路径是目录 → 返回错误\n\n"
        "**边界条件**:\n"
        "- 需要删除目录 → 用 exec rm -rf / rmdir\n"
        "- 比 exec rm 更安全：workspace 保护 + 自动验证\n\n"
        "**极简案例**: delete_file(path='temp.txt')\n"
        "→ 删除文件，返回确认"
    )

    _pre_validators = [PathExists("path"), PathType("path", "file")]
    _post_validators = [FileDeleted("path")]

    async def execute(self, path: str = "", **kwargs: Any) -> str:
        resolved = self._resolve(path)
        resolved.unlink()
        return f"Deleted: {resolved}"
