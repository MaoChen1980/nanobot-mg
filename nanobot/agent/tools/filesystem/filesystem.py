"""Filesystem tools — re-exports from split modules for backward compatibility."""

from nanobot.agent.tools.filesystem.filesystem_read import ReadFileTool
from nanobot.agent.tools.filesystem.filesystem_write import WriteFileTool
from nanobot.agent.tools.filesystem.filesystem_edit import EditFileTool, _find_match
from nanobot.agent.tools.filesystem.filesystem_list import ListDirTool

# Re-export shared helpers for external usage
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool, _resolve_path, _is_blocked_device, _parse_page_range

__all__ = [
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ListDirTool",
    "_FsTool",
    "_resolve_path",
    "_is_blocked_device",
    "_parse_page_range",
]
