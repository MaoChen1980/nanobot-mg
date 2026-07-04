"""Shell execution tool."""

from __future__ import annotations

import tempfile
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.sandbox import wrap_command
from nanobot.agent.tools.schema import p, build_parameters_schema
from nanobot.agent.tools.shell_validators import DANGEROUS_PATTERNS, check_command_safety
from nanobot.config.paths import get_runtime_subdir

_IS_WINDOWS = sys.platform == "win32"

# Pattern → tool suggestion mapping for common shell commands that have a
# dedicated nanobot tool. Used by ExecTool._suggest_tool() to nudge the LLM
# toward tool usage instead of exec.
# Order: (command_prefix_pattern, tool_call_hint, reason_suffix)
# PowerShell aliases (Get-Content, Get-ChildItem, Select-String, etc.) are
# merged into the same patterns — they are matched against the inner command
# extracted from "powershell -Command \"...\"" by _extract_powershell_inner().
_TOOL_SUGGESTIONS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r'^(?:cat|type|gc|Get-Content)\s+', re.IGNORECASE), "read_file(path=...)", "handles text, images, PDFs, and Office docs"),
    (re.compile(r'^(?:grep|findstr|sls|Select-String)\s+', re.IGNORECASE), "grep(pattern=..., path=...)", "search file contents with regex"),
    (re.compile(r'^(?:ls|dir|gci|Get-ChildItem)\s+', re.IGNORECASE), "glob(pattern=*, path=...)", "list directory contents"),
    (re.compile(r'^find\s+', re.IGNORECASE), "glob(pattern=...)", "find files matching a pattern"),
    (re.compile(r'^curl\s+', re.IGNORECASE), "web_fetch(url=...)", "fetch URL content"),
    (re.compile(r'^wget\s+', re.IGNORECASE), "web_fetch(url=...)", "fetch URL content"),
    (re.compile(r'^(?:Clear-Content|Set-Content|Add-Content|sc|ac)\s+', re.IGNORECASE), "write_file(path=..., content=...)", "write content to a file"),
]



def _extract_powershell_inner(command: str) -> str | None:
    """Extract the inner command from a powershell -Command invocation.

    Returns the command text inside -Command, or None if this isn't a
    PowerShell invocation.  Handles both quoted and unquoted forms::

      powershell -Command "Get-ChildItem -Name"
      pwsh -Command Get-Content file.txt
    """
    m = re.match(
        r'^(?:powershell|pwsh|powershell\.exe|pwsh\.exe)\s+'
        r'(?:-[cC]ommand\s+)?(.+)$',
        command,
    )
    if not m:
        return None
    inner = m.group(1).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ('"', "'"):
        inner = inner[1:-1]
    return inner.strip()

@tool_parameters(
    build_parameters_schema(
        command=p("string", "The shell command to execute. Not needed when from_cache is set."),
        working_dir=p("string", "Absolute path to the working directory. **Required.**"),
        timeout=p("integer",
            "Timeout in seconds. Increase for long-running commands like compilation or installation.",
            minimum=1, maximum=7200, default=60,
        ),
        capture_file=p("string",
            "If set, write command output to this absolute file path "
            "in real-time as it runs. "
            "You can use read_file on this path mid-execution to see partial progress "
            "before the command finishes. "
            "Useful for long commands like npm install or compilation."
        ),
        grep=p("string",
            "If set, filter cached output to only show lines containing this pattern "
            "(pure Python substring match, cross-platform). "
            "Combine with from_cache to re-examine previous output without re-executing."
        ),
        extract=p("string",
            "If set, run this command against the cached output .txt file. "
            "Use {cache} as placeholder for the cache file path. "
            "Runs with a 30-second timeout. "
            "Example: extract=\"python -c \\\"import sys; d=open('{cache}').read(); print(d.count('FAILED'))\\\"\""
        ),
        from_cache=p("string",
            "Path to a previous cache file (shown when the command was first run). "
            "Skip execution and operate on cached output. "
            "Use cases: (a) context was compressed and you lost previous output, "
            "(b) you want to grep for specific lines or extract data from prior output "
            "without re-running a slow command. "
            "Combine with grep/extract to re-examine, or use alone to see full cached output."
        ),
        verify=p("string",
            "Post-execution checks: comma-separated list (no shell needed). "
            "Available checks:\n"
            "  exit:N                  — expect exit code N (default 0)\n"
            "  output_contains:text    — output must contain text\n"
            "  output_not_contains:text— output must NOT contain text\n"
            "  file_created:path       — file must exist after command\n"
            "  file_deleted:path       — file must not exist after command\n"
            "  file_contains:path:text — file must contain text (e.g. log check)\n"
            "  file_not_contains:path:text — file must NOT contain text\n"
            "Example: verify=\"exit:0,output_contains:Build OK,file_created:dist/app.exe\""
        ),
        check=p("string",
            "Post-execution validation script (shell command). "
            "Runs after the main command. Exit 0 = pass, non-zero = fail.\n"
            "Use {cache} for the cached output file path.\n"
            "Examples:\n"
            "  check=\"python -c \\\"import sys; data=open('{cache}').read(); sys.exit(0 if 'PASS' in data else 1)\\\"\"\n"
            "  check=\"test -f dist/app.exe\""
        ),
        danger_override=p("boolean",
            "When true, bypasses danger detection and allows potentially dangerous commands. "
            "Use only after verifying the operation is safe. "
            "Default: false. Detection re-enables automatically for the next call.",
            default=False,
        ),
        required=[],
    )
)
class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        sandbox: str = "",
        path_append: str = "",
        allowed_env_keys: list[str] | None = None,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.sandbox = sandbox
        self.deny_patterns = deny_patterns if deny_patterns is not None else DANGEROUS_PATTERNS
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.allowed_env_keys = allowed_env_keys or []

        if _IS_WINDOWS:
            self.instruction = (
                "Execute shell commands. The shell is cmd.exe, not bash or PowerShell. "
                "Use cmd.exe syntax: `type` not `cat`, `dir` not `ls`, `findstr` not `grep`. "
                "Avoid PowerShell-only commands (Get-Content, Select-String, Measure-Object). "
                "Only use exec for tasks not covered by dedicated tools."
            )
        else:
            self.instruction = "Execute shell commands. Only use for tasks not covered by dedicated tools."

    name = "exec"

    _MAX_TIMEOUT = 7200
    _MAX_OUTPUT = 10_000
    _CACHE_DIR_NAME = "exec_cache"
    _DEFAULT_TAIL_LINES = 5
    _TAIL_LINES_ON_ERROR = 15

    # Windows NT status code → human-readable explanation
    _EXIT_CODE_HINTS: dict[int, str] = {
        -1073741819: "STATUS_ACCESS_VIOLATION (the process crashed — memory access error)",
        -1073741502: "STATUS_DLL_NOT_FOUND (a required DLL could not be loaded)",
        -1073741515: "STATUS_CONTROL_C_EXIT (process terminated by Ctrl+C / taskkill)",
        -1073741795: "STATUS_STACK_BUFFER_OVERRUN (stack corruption detected)",
        -1073741676: "STATUS_HEAP_CORRUPTION (heap memory corruption)",
        -1073740968: "STATUS_STACK_OVERFLOW (stack overflow)",
        255:         "cmd.exe internal error or child process crashed (exit 255)",
    }

    description = (
        "Execute shell commands for computation and scripting. "
        "Supports timeout, working directory, verification checks, "
        "output caching, grep/extract on output, and danger detection."
    )

    exclusive = True

    @staticmethod
    def _suggest_tool(command: str) -> str | None:
        """Suggest a tool alternative if the command matches a known pattern.

        Returns a formatted suggestion string, or None if exec is appropriate.
        Only triggers on simple commands — piped/compound commands are skipped.
        """
        for op in ("|", "&&", "||", ";"):
            if op in command:
                return None
        stripped = command.strip()

        # Match against full command (cat file.txt, grep foo, etc.)
        for pat, tool_call, reason in _TOOL_SUGGESTIONS:
            if pat.search(stripped):
                return f"Suggestion: Use `{tool_call}` instead of exec — {reason}."

        # For powershell -Command "...", extract the inner command and re-check.
        # This catches e.g. "powershell -Command \"Get-ChildItem -Name\"" where
        # the command starts with "powershell", not "ls".
        inner = _extract_powershell_inner(stripped)
        if inner:
            for pat, tool_call, reason in _TOOL_SUGGESTIONS:
                if pat.search(inner):
                    return f"Suggestion: Use `{tool_call}` instead of exec — {reason}."
            # PowerShell inner command doesn't need sed/git checks below
            return None

        # Special case: sed -i (in-place edit)
        lower = stripped.lower()
        if lower.startswith("sed") and " -i" in lower:
            return "Suggestion: Use `edit_file(path=..., old_string=..., new_string=...)` instead of `sed -i`."
        # git log/show
        if lower.startswith("git"):
            rest = lower[3:].strip()
            if rest.startswith("log"):
                return "Suggestion: Use `list_checkpoints(path)` to browse checkpoint history."
            if rest.startswith("show"):
                return "Suggestion: Use `list_checkpoints(path, sha=...)` to inspect a specific checkpoint."
        return None

    async def execute(
        self, command: str = "", working_dir: str | None = None,
        timeout: int | None = None, capture_file: str | None = None,
        grep: str | None = None, extract: str | None = None,
        from_cache: str | None = None,
        verify: str | None = None, check: str | None = None,
        danger_override: bool = False,
        **kwargs: Any,
    ) -> str:
        # ── Tool suggestion nudge ──
        # Check if this command could be done by a dedicated tool, and if so,
        # prepend a suggestion. This teaches the LLM in real-time.
        suggestion = ""
        if command and not from_cache:
            s = self._suggest_tool(command)
            if s:
                suggestion = s + "\n\n"

        # ── from_cache mode: skip execution, operate on cached output ──
        if from_cache:
            return await self._from_cache_mode(from_cache, grep, extract)

        if not command:
            return "Error: command is required (or use from_cache to re-examine cached output)."

        cwd = working_dir or self.working_dir
        if not cwd:
            return "Error: working_dir is required."
        if not os.path.isabs(cwd):
            return "Error: working_dir must be an absolute path."

        # Prevent an LLM-supplied working_dir from escaping the configured
        # workspace when restrict_to_workspace is enabled (#2826). Without
        # this, a caller can pass working_dir="/etc" and then all absolute
        # paths under /etc would pass the _guard_command check that anchors
        # on cwd.
        if self.restrict_to_workspace and self.working_dir:
            try:
                requested = Path(cwd).expanduser().resolve()
                workspace_root = Path(self.working_dir).expanduser().resolve()
            except Exception:
                logger.warning("Failed to resolve working_dir")
                return "Error: working_dir could not be resolved"
            if requested != workspace_root and workspace_root not in requested.parents:
                return "Error: working_dir is outside the configured workspace"

        guard_error = self._guard_command(command, cwd, danger_override)
        if guard_error:
            return guard_error

        if self.sandbox:
            if _IS_WINDOWS:
                logger.warning(
                    "Sandbox '{}' is not supported on Windows; running unsandboxed",
                    self.sandbox,
                )
            else:
                workspace = self.working_dir or cwd
                command = wrap_command(self.sandbox, command, workspace, cwd)
                cwd = str(Path(workspace).resolve())

        effective_timeout = min(timeout or self.timeout, self._MAX_TIMEOUT)
        env = self._build_env()

        if _IS_WINDOWS:
            cwd = os.path.normpath(cwd)
            # Detect cross-platform traps that silently do the wrong thing in cmd.exe.
            # Do NOT add detections for commands that produce an actual error — the
            # natural error message is sufficient for the LLM to self-correct.
            if re.search(r'(>>?|2>>?|>&1)\s*\$null\b', command):
                return (
                    "Error: '$null' after redirection creates a literal file named '$null' "
                    "in cmd.exe. Use 'NUL' to discard output on Windows."
                )
            if re.search(r'\bmkdir\s+-p\b', command):
                return (
                    "Error: 'mkdir -p' is bash syntax. Windows mkdir creates parent "
                    "directories automatically; use 'mkdir' without '-p'."
                )

        if self.path_append:
            if _IS_WINDOWS:
                env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append
            else:
                env["NANOBOT_PATH_APPEND"] = self.path_append
                command = f'export PATH="$PATH{os.pathsep}$NANOBOT_PATH_APPEND"; {command}'

        try:
            capture_path = Path(capture_file) if capture_file else None
            capture_fh = None
            if capture_path:
                if self.restrict_to_workspace and self.working_dir:
                    try:
                        cap_resolved = capture_path.expanduser().resolve()
                        ws_root = Path(self.working_dir).expanduser().resolve()
                    except Exception:
                        logger.warning("Failed to resolve capture_file path")
                        return "Error: capture_file path could not be resolved"
                    if cap_resolved != ws_root and ws_root not in cap_resolved.parents:
                        return "Error: capture_file path is outside the configured workspace"
                capture_path.parent.mkdir(parents=True, exist_ok=True)
                capture_fh = open(capture_path, "w", encoding="utf-8")

            try:
                process = None
                process = await self._spawn(command, cwd, env)

                # Stream both stdout and stderr to temp files to avoid
                # in-memory accumulation and pipe-buffer deadlocks.
                _cleanup_tmp_files = []
                _stdout_tmp = tempfile.mkstemp(suffix=".stdout", prefix="exec_")
                _stdout_path = _stdout_tmp[1]
                _cleanup_tmp_files.append(_stdout_path)
                os.close(_stdout_tmp[0])
                _stderr_tmp = tempfile.mkstemp(suffix=".stderr", prefix="exec_")
                _stderr_path = _stderr_tmp[1]
                _cleanup_tmp_files.append(_stderr_path)
                os.close(_stderr_tmp[0])

                try:
                    # Drain both streams concurrently to prevent pipe deadlocks.
                    async def _drain(stream, path):
                        with open(path, "wb") as _f:
                            while True:
                                _chunk = await stream.read(65536)
                                if not _chunk:
                                    break
                                _f.write(_chunk)

                    await asyncio.wait_for(
                        asyncio.gather(
                            _drain(process.stdout, _stdout_path),
                            _drain(process.stderr, _stderr_path),
                        ),
                        timeout=effective_timeout,
                    )

                    # Save full output to cache before truncating
                    _stdout_bytes = Path(_stdout_path).read_bytes()
                    _stderr_bytes = Path(_stderr_path).read_bytes()
                    stdout_full = _stdout_bytes.decode("utf-8", errors="replace")
                    stderr_full = _stderr_bytes.decode("utf-8", errors="replace")

                    if capture_fh:
                        capture_fh.write(stdout_full)
                        capture_fh.flush()

                    await process.wait()

                    # Truncate stdout for inline result
                    if len(_stdout_bytes) > self._MAX_OUTPUT:
                        stdout_text = _stdout_bytes[-self._MAX_OUTPUT:].decode("utf-8", errors="replace")
                    else:
                        stdout_text = stdout_full

                    # Truncate stderr for inline result
                    if len(_stderr_bytes) > self._MAX_OUTPUT:
                        _display = _stderr_bytes[-self._MAX_OUTPUT:]
                        _prefix = "... ({:,} chars truncated) ...".format(
                            len(_stderr_bytes) - self._MAX_OUTPUT
                        )
                        stderr_text = _prefix + chr(10) + _display.decode("utf-8", errors="replace")
                    else:
                        stderr_text = stderr_full
                    output_parts = [stdout_text] if stdout_text else []

                except asyncio.TimeoutError:
                    await self._kill_process(process)
                    logger.warning("Command timed out after {}s", effective_timeout)
                    _partial = Path(_stdout_path).read_bytes() if Path(_stdout_path).stat().st_size else b""
                    stdout_text = _partial.decode("utf-8", errors="replace")
                    _partial_err = Path(_stderr_path).read_bytes() if Path(_stderr_path).stat().st_size else b""
                    partial_stderr = _partial_err.decode("utf-8", errors="replace").strip() if _partial_err else ""
                    msg = "Error: Command timed out after {} seconds".format(effective_timeout)
                    if stdout_text:
                        msg += chr(10) + "STDOUT:" + chr(10) + stdout_text
                    if partial_stderr:
                        msg += chr(10) + "STDERR:" + chr(10) + partial_stderr
                    return msg

            except Exception:
                if process:
                    await self._kill_process(process)
                    logger.warning(
                        "Killed running process on setup failure (pid={})",
                        process.pid,
                    )
                raise

            finally:
                if capture_fh:
                    capture_fh.close()
                if process:
                    for _s in (process.stdout, process.stderr):
                        if _s:
                            try:
                                _s.close()
                            except Exception:
                                pass
                try:
                    for _p in _cleanup_tmp_files:
                        try:
                            Path(_p).unlink(missing_ok=True)
                        except Exception:
                            pass
                except NameError:
                    pass

            exit_code = process.returncode
            if exit_code != 0:
                hint = self._EXIT_CODE_HINTS.get(exit_code, "")
                hint_suffix = f" — {hint}" if hint else ""
                logger.warning("Command exit with code {} (shell=cmd, cwd={}, cmd={:.80}){}",
                               exit_code, cwd, command, hint_suffix)

            cwd_safe = cwd.replace('\\', '/')
            hint = self._EXIT_CODE_HINTS.get(exit_code, "")
            hint_suffix = f" — {hint}" if hint else ""
            status_line = f"Exit: {exit_code}  |  cwd: {cwd_safe}  |  shell: {'cmd' if _IS_WINDOWS else 'sh'}{hint_suffix}"

            body = "\n".join(output_parts) if output_parts else "(no output)"
            SEP = "─" * 56
            result = f"{status_line}\n{SEP}\n{body}"

            # If command failed and stderr wasn't already in output_parts, append it
            if exit_code != 0 and stderr_text and stderr_text.strip():
                stderr_in_body = any("STDERR:" in p for p in output_parts)
                if not stderr_in_body:
                    result += f"\nSTDERR:\n{stderr_text}"

            max_len = self._MAX_OUTPUT
            if len(result) > max_len:
                half = max_len // 2
                result = (
                    result[:half]
                    + f"\n\n... ({len(result) - max_len:,} chars truncated) ...\n\n"
                    + result[-half:]
                )

            # Save full output to cache
            cache_path = self._save_to_cache(command, stdout_full, stderr_full, process.returncode)

            # Post-execution verification (verify + check)
            verification = ""
            if verify or check:
                verification = await self._run_verification(
                    verify or "", check or "", stdout_full, stderr_full,
                    process.returncode, cache_path, cwd, env,
                )

            # Route through grep/extract modes
            if grep:
                return suggestion + self._format_grep_result(stdout_text, stderr_text, process.returncode, grep) + verification
            if extract:
                return suggestion + (await self._format_extract_result(
                    cache_path, stdout_text, stderr_text, process.returncode, extract,
                )) + verification

            # Default: append cache path to result
            result += f"\n{SEP}\n[Full output cached: {cache_path}]"
            return suggestion + result + verification

        except Exception as e:
            logger.exception("Command execution failed")
            return f"Error executing command: {str(e)}"

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _save_to_cache(self, command: str, stdout: str, stderr: str, exit_code: int) -> Path:
        """Save command output to .json and .txt cache files and return the .json path."""
        cache_dir = get_runtime_subdir(self._CACHE_DIR_NAME)
        cmd_hash = hashlib.sha256(command.encode()).hexdigest()[:12]
        ts = int(time.time())
        cache_path = cache_dir / f"{cmd_hash}_{ts}.json"

        cache_data = {
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timestamp": ts,
        }
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")

        # Also write plain text for easy reading
        txt_path = cache_path.with_suffix(".txt")
        combined = stdout
        if stderr.strip():
            combined += f"\n\nSTDERR:\n{stderr}"
        txt_path.write_text(combined, encoding="utf-8")

        logger.debug("Saved exec output to cache: {}", cache_path)
        return cache_path

    def _load_from_cache(self, cache_path_str: str) -> dict:
        """Load cached output from a JSON cache file."""
        path = Path(cache_path_str)
        if not path.exists():
            raise FileNotFoundError(f"Cache file not found: {cache_path_str}")
        return json.loads(path.read_text(encoding="utf-8"))

    async def _from_cache_mode(self, cache_path: str, grep: str | None, extract: str | None) -> str:
        """Re-examine cached output without re-executing."""
        try:
            data = self._load_from_cache(cache_path)
        except FileNotFoundError as e:
            return f"Error: {e}"
        except json.JSONDecodeError:
            return f"Error: Invalid cache file: {cache_path}"

        exit_code = data["exit_code"]
        stdout = data["stdout"]
        stderr = data["stderr"]

        if grep:
            return self._format_grep_result(stdout, stderr, exit_code, grep)
        if extract:
            txt_path = Path(cache_path).with_suffix(".txt")
            return await self._format_extract_result(txt_path, stdout, stderr, exit_code, extract)

        # Default: show full cached output
        combined = stdout
        if stderr.strip():
            combined += f"\n\nSTDERR:\n{stderr}"
        tail_lines = self._TAIL_LINES_ON_ERROR if exit_code != 0 else self._DEFAULT_TAIL_LINES
        lines = combined.splitlines()
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        tail_text = "\n".join(tail)

        result = f"Exit: {exit_code}  |  [Loaded from cache: {cache_path}]"
        if tail_text:
            result += f"\n{'─'*56}\nLast {len(tail)} lines:\n{tail_text}"
        return result

    # ------------------------------------------------------------------
    # grep
    # ------------------------------------------------------------------

    @staticmethod
    def _format_grep_result(stdout: str, stderr: str, exit_code: int, pattern: str) -> str:
        """Filter output lines matching pattern and return compact result."""
        combined = stdout
        if stderr.strip():
            combined += f"\n{stderr}"
        lines = combined.splitlines()
        matched = [(i + 1, line) for i, line in enumerate(lines) if pattern in line]

        if not matched:
            return f"[No lines matched {pattern!r}]\nExit code: {exit_code}"

        result_lines = [f"[Filtered: {len(matched)} of {len(lines)} lines matching {pattern!r}]"]
        for line_no, text in matched:
            display = text if len(text) <= 200 else text[:197] + "..."
            result_lines.append(f"  {line_no}:{display}")

        result_lines.append(f"Exit code: {exit_code}")
        return "\n".join(result_lines)

    # ------------------------------------------------------------------
    # extract
    # ------------------------------------------------------------------

    async def _format_extract_result(
        self, cache_path: Path, stdout: str, stderr: str, exit_code: int, extract_cmd: str,
    ) -> str:
        """Run an extract command against the cached output file."""
        txt_path = cache_path.with_suffix(".txt")
        # Ensure text file exists
        if not txt_path.exists():
            combined = stdout
            if stderr.strip():
                combined += f"\n\nSTDERR:\n{stderr}"
            txt_path.parent.mkdir(parents=True, exist_ok=True)
            txt_path.write_text(combined, encoding="utf-8")

        cmd = extract_cmd.replace("{cache}", str(txt_path))
        try:
            proc = await self._spawn(cmd, str(txt_path.parent), self._build_env())
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            err = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            parts = ["[Extract result]:"]
            if out.strip():
                parts.append(out.rstrip())
            if err.strip():
                parts.append(f"STDERR:\n{err}")
            parts.append(f"Exit code: {proc.returncode}")
            return "\n".join(parts)
        except asyncio.TimeoutError:
            await self._kill_process(proc)
            return "[Extract timed out after 30s]"
        except Exception as e:
            return f"[Extract error: {e}]"

    # ------------------------------------------------------------------
    # Post-execution verification
    # ------------------------------------------------------------------

    async def _run_verification(
        self, verify: str, check: str, stdout: str, stderr: str,
        exit_code: int, cache_path: Path, cwd: str, env: dict,
    ) -> str:
        """Run both declarative verify and script-based check, return formatted block."""
        parts: list[str] = []
        has_fail = False

        if verify:
            v_result, v_fail = self._run_verify(verify, stdout, stderr, exit_code)
            if v_result:
                parts.append(v_result)
                if v_fail:
                    has_fail = True

        if check:
            c_result, c_fail = await self._run_check(check, cache_path, cwd, env)
            if c_result:
                parts.append(c_result)
                if c_fail:
                    has_fail = True

        if not parts:
            return ""

        joined = "\n".join(parts)
        header = "[Verification]" + (" ❌" if has_fail else " ✓")
        return "\n" + header + "\n" + joined

    @staticmethod
    def _run_verify(verify: str, stdout: str, stderr: str, exit_code: int) -> tuple[str, bool]:
        """Run declarative verify checks. Returns (formatted_block, has_failure)."""
        combined = stdout
        if stderr.strip():
            combined += "\n" + stderr
        checks = [c.strip() for c in verify.split(",")]
        lines: list[str] = []
        has_fail = False

        for check_item in checks:
            if not check_item:
                continue
            if check_item.startswith("exit:"):
                expected = int(check_item.split(":", 1)[1])
                ok = exit_code == expected
                lines.append(f"  {'✓' if ok else '❌'} exit={exit_code}" + ("" if ok else f" (expected {expected})"))
                if not ok:
                    has_fail = True
            elif check_item.startswith("output_contains:"):
                text = check_item.split(":", 1)[1]
                ok = text in combined
                lines.append(f"  {'✓' if ok else '❌'} output contains {text!r}")
                if not ok:
                    has_fail = True
            elif check_item.startswith("output_not_contains:"):
                text = check_item.split(":", 1)[1]
                ok = text not in combined
                lines.append(f"  {'✓' if ok else '❌'} output does not contain {text!r}")
                if not ok:
                    has_fail = True
            elif check_item.startswith("file_created:"):
                path = check_item.split(":", 1)[1]
                ok = Path(path).exists()
                lines.append(f"  {'✓' if ok else '❌'} file created: {path}")
                if not ok:
                    has_fail = True
            elif check_item.startswith("file_deleted:"):
                path = check_item.split(":", 1)[1]
                ok = not Path(path).exists()
                lines.append(f"  {'✓' if ok else '❌'} file deleted: {path}")
                if not ok:
                    has_fail = True
            elif check_item.startswith("file_contains:"):
                parts = check_item.split(":", 2)
                path, text = parts[1], parts[2]
                ok = Path(path).exists() and text in Path(path).read_text(encoding="utf-8", errors="replace")
                lines.append(f"  {'✓' if ok else '❌'} {path} contains {text!r}")
                if not ok:
                    has_fail = True
            elif check_item.startswith("file_not_contains:"):
                parts = check_item.split(":", 2)
                path, text = parts[1], parts[2]
                exists = Path(path).exists()
                ok = not exists or text not in Path(path).read_text(encoding="utf-8", errors="replace")
                lines.append(f"  {'✓' if ok else '❌'} {path} does not contain {text!r}")
                if not ok:
                    has_fail = True

        return "\n".join(lines) if lines else "", has_fail

    async def _run_check(self, check_cmd: str, cache_path: Path, cwd: str, env: dict) -> tuple[str, bool]:
        """Run a post-execution validation script. Returns (formatted_block, has_failure)."""
        cmd = check_cmd.replace("{cache}", str(cache_path))
        try:
            proc = await self._spawn(cmd, cwd, env)
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
            err = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
            ok = proc.returncode == 0
            parts = [f"  {'✓' if ok else '❌'} check script (exit {proc.returncode})"]
            if out.strip():
                parts.append(out.rstrip())
            if err.strip():
                parts.append(f"  STDERR: {err.strip()}")
            return "\n".join(parts), not ok
        except asyncio.TimeoutError:
            return "  ❌ check script timed out after 30s", True
        except Exception as e:
            return f"  ❌ check script error: {e}", True

    @staticmethod
    async def _spawn(
        command: str, cwd: str, env: dict[str, str],
        shell_program: str | None = None,
        login: bool = True,
        *,
        stdin: int = asyncio.subprocess.DEVNULL,
    ) -> asyncio.subprocess.Process:
        """Launch *command* in a platform-appropriate shell."""
        if _IS_WINDOWS:
            # Use cmd.exe instead of powershell.exe to avoid PowerShell
            # involvement in .bat/.cmd execution chains.  PowerShell adds an
            # extra process layer and can trigger recursive spawning when a
            # child process (e.g. conda.exe, java.exe) re-invokes PowerShell.
            return await asyncio.create_subprocess_exec(
                "cmd.exe", "/c", command,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        shell_program = shell_program or shutil.which("bash") or "/bin/bash"
        args = [shell_program]
        if login:
            args.append("-l")
        args.extend(["-c", command])
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        """Kill a subprocess and reap it to prevent zombies.

        On Windows, uses ``taskkill /T /F`` to terminate the entire
        process tree so child processes (e.g. powershell spawned by
        gradlew.bat) don't become orphans.
        """
        if _IS_WINDOWS:
            try:
                kill_proc = await asyncio.create_subprocess_exec(
                    "taskkill", "/T", "/F", "/PID", str(process.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(kill_proc.wait(), timeout=3.0)
            except OSError as e:
                logger.debug("taskkill failed for pid {}: {}", process.pid, e)
            except asyncio.TimeoutError:
                logger.debug("taskkill timed out for pid {}", process.pid)
        else:
            process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            if not _IS_WINDOWS:
                try:
                    os.waitpid(process.pid, os.WNOHANG)
                except AttributeError:
                    # frozen os module on Windows lacks WNOHANG
                    pass
                except (ProcessLookupError, ChildProcessError) as e:
                    logger.debug("Process already reaped or not found: {}", e)

    def _build_env(self) -> dict[str, str]:
        """Build a minimal environment for subprocess execution.

        On Unix, only HOME/LANG/TERM are passed; ``bash -l`` sources the
        user's profile which sets PATH and other essentials.

        On Windows, ``cmd.exe /c`` is used.  ``COMSPEC`` still points to
        ``powershell.exe`` for compatibility with PowerShell-aware tools;
        a curated set of system variables (including PATH) is forwarded.
        API keys and other secrets are still excluded.
        """
        if _IS_WINDOWS:
            sr = os.environ.get("SYSTEMROOT", r"C:\Windows")
            env = {
                "SYSTEMROOT": sr,
                "COMSPEC": os.environ.get("COMSPEC", f"{sr}\\System32\\cmd.exe"),
                "USERPROFILE": os.environ.get("USERPROFILE", ""),
                "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
                "HOMEPATH": os.environ.get("HOMEPATH", "\\"),
                "TEMP": os.environ.get("TEMP", f"{sr}\\Temp"),
                "TMP": os.environ.get("TMP", f"{sr}\\Temp"),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
                "PATH": os.environ.get("PATH", f"{sr}\\system32;{sr}"),
                "APPDATA": os.environ.get("APPDATA", ""),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "ProgramData": os.environ.get("ProgramData", ""),
                "ProgramFiles": os.environ.get("ProgramFiles", ""),
                "ProgramFiles(x86)": os.environ.get("ProgramFiles(x86)", ""),
                "ProgramW6432": os.environ.get("ProgramW6432", ""),
            }
            for key in self.allowed_env_keys:
                val = os.environ.get(key)
                if val is not None:
                    env[key] = val
            # Inject a recursion guard so any child process can detect when it
            # is running inside a nanobot-managed process tree.  Set last so
            # it cannot be overridden by allowed_env_keys.
            env["NANOBOT_RECURSION_GUARD"] = "1"
            return env
        home = os.environ.get("HOME") or tempfile.gettempdir()
        env = {
            "HOME": home,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": os.environ.get("TERM", "dumb"),
        }
        for key in self.allowed_env_keys:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        # Inject a recursion guard so any child process can detect when it
        # is running inside a nanobot-managed process tree.
        env["NANOBOT_RECURSION_GUARD"] = "1"
        return env

    def _guard_command(self, command: str, cwd: str, danger_override: bool = False) -> str | None:
        """Safety guard for potentially destructive commands.

        Returns a warning string (not ``"Error"``) when danger is detected,
        so the LLM can reconsider and retry with ``danger_override=true``.
        """
        return check_command_safety(
            command=command,
            cwd=cwd,
            deny_patterns=self.deny_patterns,
            allow_patterns=self.allow_patterns,
            restrict_to_workspace=self.restrict_to_workspace,
            workspace_root=self.working_dir,
            danger_override=danger_override,
        )

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        # Windows: match drive-root paths like `C:\` as well as `C:\path\to\file`
        # NOTE: `*` is required so `C:\` (nothing after the slash) is still extracted.
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]*", command)
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths

