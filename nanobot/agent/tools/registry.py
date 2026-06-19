"""Tool registry for dynamic tool management."""

from __future__ import annotations

from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.output_cache import OutputCache


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self, output_cache: OutputCache | None = None):
        self._tools: dict[str, Tool] = {}
        self._cached_definitions: list[dict[str, Any]] | None = None
        self._cache = output_cache or OutputCache()

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """Extract a normalized tool name from either OpenAI or flat schemas."""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions with stable ordering for cache-friendly prompts.

        Built-in tools are sorted first as a stable prefix, then MCP tools are
        sorted and appended.  The result is cached until the next
        register/unregister call.
        """
        if self._cached_definitions is not None:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = self._schema_name(schema)
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        self._cached_definitions = builtins + mcp_tools
        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Tool | None, dict[str, Any], str | None]:
        """Resolve, cast, and validate one tool call."""
        # Guard against invalid parameter types (e.g., list instead of dict)
        if not isinstance(params, dict) and name in ('write_file_tool', 'read_file_tool'):
            return None, params, (
                f"Error: Tool '{name}' parameters must be a JSON object, got {type(params).__name__}. "
                "Use named parameters: tool_name(param1=\"value1\", param2=\"value2\")"
            )

        tool = self._tools.get(name)
        if not tool:
            available = self.tool_names
            suggestion = ""
            # Try to suggest a similar tool name
            similar = [n for n in available if name.lower() in n.lower() or n.lower() in name.lower()]
            if similar:
                suggestion = f" Did you mean: {', '.join(similar)}?"
            return None, params, (
                f"Error: Tool '{name}' not found.{suggestion} "
                f"Available tools ({len(available)}): {', '.join(available[:10])}"
                + (" ..." if len(available) > 10 else "")
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            schema = tool.parameters
            props = schema.get("properties", {})
            # Build a hint showing expected param types
            expected = ", ".join(
                f"{k}({props[k].get('type', 'any')})" for k in (schema.get("required") or []) if k in props
            )
            hint = f" Required: {expected}." if expected else ""
            return tool, cast_params, (
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + hint
            )
        return tool, cast_params, None

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        """Execute a tool by name with given parameters.

        The execution pipeline is:

        1. :meth:`prepare_call` — resolve tool, cast params, validate schema
        2. **Pre-validators** — run :attr:`Tool._pre_validators`, abort on failure
        3. **Execute** — call ``tool.execute(**params)``
        4. **Post-validators** — run :attr:`Tool._post_validators`, collect warnings
        5. **Format result** — append ``❌`` on error, ``✓`` on single-line success
        """
        tool, params, error = self.prepare_call(name, params)
        if error:
            return error

        # Cache hit for read_only tools — skip execution
        if tool.read_only:
            cached = self._cache.get(name, params)
            if cached is not None:
                cached_result, age = cached
                return self._format_result(name, cached_result) + f"\n(cached {age}s ago)"

        # Pre-validators
        for v in tool._pre_validators:
            err = await v.check(tool, params)
            if err:
                return f"{v.__class__.__name__}: {err}"

        # Execute
        try:
            result = await tool.execute(**params)
        except Exception as e:
            logger.exception("Tool '{}' execution failed", name)
            return f"Error executing {name}: {str(e)}"

        # If tool itself reported an error, skip post-validators
        if isinstance(result, str) and result.startswith("Error"):
            return result

        # Danger warnings pass through as-is — not errors, no ❌
        if isinstance(result, str) and result.startswith("⚠️ Danger:"):
            return result

        # Post-validators
        warnings: list[str] = []
        for v in tool._post_validators:
            warn = await v.check(tool, params, result)
            if warn:
                warnings.append(f"⚠️ {v.__class__.__name__}: {warn}")

        if warnings:
            result = str(result) + "\n" + "\n".join(warnings)

        result_str = str(result)

        # Cache successful result for read_only tools
        if tool.read_only:
            self._cache.put(name, params, result_str)

        # Check dedup — same content returned within recent history
        if self._cache.check_duplicate(result_str):
            return "[Content unchanged since previous tool call — see earlier output for the full content.]"

        return self._format_result(name, result)

    @staticmethod
    def _format_result(name: str, result: Any) -> str:
        """Standardize result formatting.

        Danger warnings pass through as-is.
        """
        if isinstance(result, str) and result.startswith("⚠️ Danger:"):
            return result
        return str(result)

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
