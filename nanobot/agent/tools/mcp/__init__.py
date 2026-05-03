"""MCP tools — re-exports from mcp.py for backward compatibility."""

from nanobot.agent.tools.mcp.mcp import (
    MCPToolWrapper,
    MCPResourceWrapper,
    MCPPromptWrapper,
    _is_transient,
    _normalize_windows_stdio_command,
    _sanitize_name,
    connect_mcp_servers,
)

__all__ = [
    "MCPToolWrapper",
    "MCPResourceWrapper",
    "MCPPromptWrapper",
    "_is_transient",
    "_normalize_windows_stdio_command",
    "_sanitize_name",
    "connect_mcp_servers",
]