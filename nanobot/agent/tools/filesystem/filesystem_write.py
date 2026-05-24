from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema
from .filesystem_base import _FsTool
from nanobot.agent.tools import file_state

@tool_parameters(
    build_parameters_schema(
        path=p("string", "Absolute path to a file to write. OVERWRITES existing file, auto-creates parent directories."),
        content=p("string", "Full file content to write. Replaces entire file — use edit_file for partial edits or substitutions."),
        then_exec=p("string",
            "If set to a shell command string, executes it after writing (and after then_check if set) "
            "and returns the command output. Working directory is the written file's parent. "
            "Example: 'python main.py'. Useful for script-then-run workflows."
        ),
        then_check=p("string",
            "If set, type-checks the file after writing before executing then_exec. "
            "Type checker to use: 'pyright' (Python) or 'tsc' (TypeScript/JavaScript). Returns pass/fail + errors. "
            "Works with then_exec: write → check → exec."
        ),
        then_grep=p("string",
            "If set, searches the written file for this exact substring (not a regex) after saving, "
            "and returns matching line numbers and content. "
            "Helps verify the write landed correctly without re-reading the entire file."
        ),
        required=["path", "content"],
    )
)
class WriteFileTool(_FsTool):
    """Write content to a file. Overwrites if it exists; creates parent dirs as needed."""

    name = "write_file"

    description = (
        "**用途**: 创建新文件或整体覆写已有文件。\n\n"
        "**什么时候用**:\n"
        "- 需要创建新文件时\n"
        "- 需要整体替换文件内容时\n"
        "- 需要写文件后自动验证、类型检查或执行命令时\n\n"
        "**后处理链**（按顺序执行）:\n"
        "- `then_grep` — 写入后搜索指定字符串，验证写入成功，不重新读取整个文件\n"
        "- `then_check` — 写入后运行类型检查（pyright/tsc），返回 pass/fail + 错误详情\n"
        "- `then_exec` — 写入并验证后执行一条 shell 命令（工作目录为文件所在目录）\n"
        "三者可组合使用，e.g. write → then_check → then_exec。\n\n"
        "**什么时候不用**:\n"
        "- 只需要修改文件中的部分内容 → 用 edit_file\n"
        "- 只需要读取文件 → 用 read_file\n"
        "- 需要移动/重命名文件 → 用 move_file\n"
    )

    async def execute(
        self, path: str | None = None, content: str | None = None,
        then_exec: str | None = None,
        then_check: str | None = None,
        then_grep: str | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = self._resolve(path)

            # Read-before-write check: warn if overwriting unread content
            warning = file_state.check_read(fp)

            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            file_state.record_write(fp)
            write_result = f"Successfully wrote {len(content)} characters to {fp.as_posix()}"
            if warning:
                write_result = f"{warning}\n{write_result}"

            # Auto-verify: extract first meaningful line from content as verification
            content_lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
            verify_pattern = content_lines[0] if content_lines else None
            verify_result = ""
            if verify_pattern:
                verify_result = self._find_in_file(fp, verify_pattern, max_matches=3)

            # Type-check before exec (if requested)
            check_result = ""
            if then_check:
                check_result = await self._run_type_check(fp, then_check)

            # Execute if requested
            exec_result = ""
            if then_exec:
                from nanobot.agent.tools.shell import ExecTool
                exec_tool = ExecTool(
                    working_dir=str(fp.parent),
                    restrict_to_workspace=False,
                )
                exec_result = f"\n\nExec output:\n{await exec_tool.execute(then_exec)}"

            parts = [write_result]
            if verify_result:
                parts.append(f"Verified:\n{verify_result}")
            if check_result:
                parts.append(check_result)
            if exec_result:
                parts.append(exec_result)
            if then_grep:
                parts.append(self._find_in_file(fp, then_grep))
            return "\n".join(parts)
        except PermissionError as e:
            logger.warning("WriteFile permission denied: {}", e)
            return f"Error: {e}"
        except Exception as e:
            logger.warning("WriteFile failed: {}", e)
            return f"Error writing file: {e}"

    async def _run_type_check(self, fp: Path, checker: str) -> str:
        """Run a type checker on the written file. Returns pass/fail summary."""
        if checker == "auto":
            ext = fp.suffix.lower()
            if ext == ".py":
                checker = "pyright"
            elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".mts", ".cts"):
                checker = "tsc"
            else:
                return f"\nCheck: skipped (no checker for '{ext}')"

        from nanobot.agent.tools.shell import ExecTool

        wd = self._workspace or fp.parent
        exec_tool = ExecTool(working_dir=str(wd), restrict_to_workspace=False)

        if checker == "pyright":
            cmd = f"npx --prefix tools pyright {fp} --outputjson"
            raw = await exec_tool.execute(cmd)
            return self._format_pyright_result(raw)
        elif checker == "tsc":
            cmd = f"npx --prefix tools tsc --noEmit --allowJs --checkJs {fp}"
            raw = await exec_tool.execute(cmd)
            return self._format_tsc_result(raw)
        else:
            return f"\nCheck: unknown checker '{checker}' (use 'auto', 'pyright', or 'tsc')"

    @staticmethod
    def _format_pyright_result(raw: str) -> str:
        """Parse pyright --outputjson and return a readable summary."""
        try:
            import json
            lines = raw.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if not in_json and line.strip().startswith("{"):
                    in_json = True
                if in_json:
                    json_lines.append(line)
                    if line.strip() == "}":
                        break
            if not json_lines:
                return f"\nCheck: pyright output (raw):\n{raw[:500]}"
            data = json.loads("\n".join(json_lines))
            summary = data.get("summary", {})
            errors = summary.get("errorCount", 0)
            warnings = summary.get("warningCount", 0)
            diags = data.get("generalDiagnostics", [])
            if errors == 0 and warnings == 0:
                return "\nCheck: PASSED (pyright)"
            lines_out = [f"\nCheck: FAILED — {errors} errors, {warnings} warnings (pyright)"]
            for d in diags[:5]:
                lines_out.append(f"  line {d['range']['start']['line']}: {d['message']}")
            if len(diags) > 5:
                lines_out.append(f"  ... and {len(diags) - 5} more")
            return "\n".join(lines_out)
        except Exception:
            logger.warning("Failed to parse pyright output")
            return f"\nCheck: pyright output (raw):\n{raw[:500]}"

    @staticmethod
    def _format_tsc_result(raw: str) -> str:
        """Parse tsc output and return a readable summary."""
        lines = raw.strip().split("\n")
        error_lines = [l for l in lines if l and "error TS" in l]
        if not error_lines:
            return "\nCheck: PASSED (tsc)"
        summary = f"\nCheck: FAILED — {len(error_lines)} type errors (tsc)"
        body = "\n".join(f"  {l}" for l in error_lines[:5])
        tail = f"\n  ... and {len(error_lines) - 5} more" if len(error_lines) > 5 else ""
        return f"{summary}\n{body}{tail}"
