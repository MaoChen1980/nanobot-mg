"""Recipe tool — multi-step operations composed from other tools in one call."""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        recipe=p("string", "Recipe name: find_and_read, explore_source"),
        pattern=p("string", "Search pattern (for find_and_read)"),
        path=p("string", "File or directory path"),
        max_files=p("integer", "Max files to read (for find_and_read)", minimum=1, maximum=50),
    ),
    required=["recipe"],
)
class RecipeTool(Tool):
    """Execute multi-step operations by composing other tools — one call instead of many.

    Built-in recipes:
      - find_and_read: grep for pattern → read matching files
      - explore_source: explore module → read key parts
    """

    name = "run_recipe"
    read_only = True

    description = (
        "**用途**: 一次调用执行多步操作，由框架自动串联工具调用。\n\n"
        "**限制**:\n"
        "- recipe 不可配置步骤顺序或参数\n"
        "- max_files 最大 50\n"
        "- 匹配文件超过 max_files 时，按修改时间取最新的 max_files 个\n\n"
        "**内置 recipe**:\n"
        "  - find_and_read(pattern, path, max_files):\n"
        "    第 1 步: grep pattern 获取匹配文件列表\n"
        "    第 2 步: read_files 读取这些文件\n"
        "    适合：搜代码→立刻读结果\n"
        "  - explore_source(path):\n"
        "    第 1 步: explore_module 分析结构\n"
        "    第 2 步: 返回结构分析结果\n"
        "    适合：理解模块结构\n\n"
        "**错误应对**:\n"
        "- recipe 名不存在 → 返回可用列表\n"
        "- grep 无匹配 → 返回提示无文件匹配\n\n"
        "**边界条件**:\n"
        "- 只需要单步 → 直接用 grep/read_files/explore_module\n"
        "- 需要精细控制参数 → 手动分步调用\n\n"
        "**极简案例**: run_recipe(recipe='find_and_read', pattern='class.*Handler', path='src/')\n"
        "→ 两步合一：搜索 + 读取匹配文件"
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

    async def _recipe_explore_source(self, path: str = "", **kwargs: Any) -> str:
        explore = await self._call("explore_module", {"path": path, "show_refs": True})
        explore_str = str(explore)
        if explore_str.startswith("Error"):
            return explore_str

        return f"# explore_source: {path}\n\n{explore_str}"
