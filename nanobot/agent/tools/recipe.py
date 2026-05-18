"""Recipe tool — multi-step operations composed from other tools in one call."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Coroutine

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        recipe=p("string", "Recipe to execute", enum=["find_and_read", "audit_todos", "trace_function"]),
        pattern=p("string", "Search pattern. Required for: find_and_read, trace_function. Ignored by: audit_todos."),
        path=p("string", "Absolute path to a file or directory."),
        max_files=p("integer", "Max files to read (for find_and_read, default 10)", minimum=1, maximum=50, default=10),
    ),
    required=["recipe"],
)
class RecipeTool(Tool):
    """Execute multi-step operations by composing other tools — one call instead of many.

    Built-in recipes:
      - find_and_read: grep for pattern → read matching files
      - audit_todos: scan TODOs/FIXMEs → group → summary report
      - trace_function: find definition → find calls → read key references
    """

    name = "run_recipe"
    read_only = True

    description = (
        "**用途**: 一次调用执行多步操作，由框架自动串联工具调用。\n\n"
        "**什么时候用**:\n"
        "- 需要多步串联的常见操作（如搜索关键词后读取文件、审计 TODO、追踪函数）\n"
        "- 想一步完成原本需要多轮的 grep → read_files 流程\n\n"
        "**什么时候不用**:\n"
        "- 需要精细控制每一步的参数 → 手动分步调用各工具\n"
        "- 只需要单步操作 → 直接用 grep/read_files\n"
    )

    def __init__(self, run_tool: Callable[[str, dict[str, Any]], Coroutine[Any, Any, Any]] | None = None):
        self._run_tool = run_tool

    async def execute(self, recipe: str = "", **kwargs: Any) -> str:
        handler_name = f"_recipe_{recipe.replace('-', '_')}"
        handler = getattr(self, handler_name, None)
        if not handler:
            available = [n.replace("_recipe_", "") for n in dir(self) if n.startswith("_recipe_")]
            return f"Error: Unknown recipe '{recipe}'. Available: {', '.join(sorted(available))}"
        return await handler(**kwargs)

    async def _call(self, tool: str, params: dict[str, Any]) -> str:
        if self._run_tool is None:
            return f"[Recipe would call {tool} with {params}]"
        result = await self._run_tool(tool, params)
        return str(result)

    # -- recipes ----------------------------------------------------------------

    async def _recipe_find_and_read(self, pattern: str = "", path: str = ".", max_files: int = 10, **kwargs: Any) -> str:
        grep_result = await self._call("grep", {
            "pattern": pattern, "path": path, "output_mode": "files_with_matches",
        })
        grep_str = str(grep_result)
        if grep_str.startswith("Error") or "No matches" in grep_str:
            return f"# find_and_read: pattern={pattern!r}\n\nNo files matched in {path}"

        read_result = await self._call("read_files", {
            "glob": "**/*", "grep": pattern, "path": path, "max_files": max_files,
        })
        return f"# find_and_read: pattern={pattern!r}\n\n{read_result}"

    async def _recipe_audit_todos(self, path: str = ".", **kwargs: Any) -> str:
        grep = await self._call("grep", {
            "pattern": "TODO|FIXME|HACK|XXX|WORKAROUND",
            "path": path,
            "output_mode": "content",
        })
        grep_str = str(grep)
        if grep_str.startswith("Error") or "No matches" in grep_str:
            return f"# TODO Audit — {path}\n\nNo TODO/FIXME/HACK found."

        lines = grep_str.strip().split("\n")
        by_type: Counter = Counter()
        by_file: Counter = Counter()
        for line in lines:
            if "TODO" in line:
                by_type["TODO"] += 1
            elif "FIXME" in line:
                by_type["FIXME"] += 1
            elif "HACK" in line or "WORKAROUND" in line:
                by_type["HACK"] += 1
            else:
                by_type["XXX"] += 1
            # Extract file path before the first ":" and "|"
            m = re.match(r"([^:|]+)", line)
            if m:
                by_file[m.group(1)] += 1

        top_files = by_file.most_common(10)
        parts = [
            f"# TODO Audit — {path}",
            "",
            f"**Total**: {len(lines)} items",
            f"**By type**: {', '.join(f'{k}={v}' for k, v in sorted(by_type.most_common()))}",
            f"**Files with issues**: {len(by_file)}",
            "",
            "**Top files**:",
        ]
        for f, c in top_files:
            parts.append(f"  {f}: {c}")

        parts.append("")
        parts.append(f"**Details** (first {min(50, len(lines))} lines):")
        parts.extend(lines[:50])
        if len(lines) > 50:
            parts.append(f"... and {len(lines) - 50} more items")

        return "\n".join(parts)

    async def _recipe_trace_function(self, pattern: str = "", path: str = ".", **kwargs: Any) -> str:
        if not pattern:
            return "Error: pattern is required for trace_function"

        # Step 1: find definition
        def_grep = await self._call("grep", {
            "pattern": rf"^(async\s+)?def\s+{pattern}|^class\s+{pattern}",
            "path": path,
            "output_mode": "content",
        })
        def_str = str(def_grep)

        # Step 2: find calls
        call_grep = await self._call("grep", {
            "pattern": rf"{pattern}\s*\(",
            "path": path,
            "output_mode": "content",
        })
        call_str = str(call_grep)

        # Step 3: read key definition file
        key_file = ""
        if not def_str.startswith("Error") and "No matches" not in def_str:
            m = re.search(r"^([^:|]+)", def_str.strip())
            if m:
                key_file = m.group(1)

        parts = [f"# Trace: {pattern} — {path}", ""]

        parts.append("## Definition")
        if not def_str.startswith("Error") and "No matches" not in def_str:
            parts.extend(def_str.strip().split("\n"))
        else:
            parts.append("  (No definition found)")

        parts.append("")
        if not call_str.startswith("Error") and "No matches" not in call_str:
            call_lines = call_str.strip().split("\n")
            parts.append(f"## Calls ({len(call_lines)})")
            parts.extend(call_lines[:30])
            if len(call_lines) > 30:
                parts.append(f"... and {len(call_lines) - 30} more calls")
        else:
            parts.append("## Calls (0)")

        if key_file:
            parts.append("")
            read_result = await self._call("read_files", {
                "glob": key_file, "path": path, "max_files": 3,
            })
            if not str(read_result).startswith("Error"):
                parts.append(f"## Key file: {key_file}")
                parts.append(str(read_result))

        return "\n".join(parts)
