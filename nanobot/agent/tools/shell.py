"""Shell execution tool."""

import asyncio
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.sandbox import wrap_command
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.config.paths import get_media_dir

_IS_WINDOWS = sys.platform == "win32"

# Executables whose -c/-e/-Command/-File flags take a quoted script argument
# that cmd.exe /c would mangle (list2cmdline → outer-quote wrappping → strip).
# Spawning these directly avoids the problem.
_EXES_WITH_SCRIPT_FLAGS: set[str] = {
    # Executables known to use -c/-e/-Command/-File flags with a quoted
    # script argument.  For these we extract the script text as a single
    # argument (preserving spaces) instead of splitting on whitespace.
    "powershell",
    "powershell.exe",
    "pwsh",
    "python",
    "python3",
    "node",
}

@tool_parameters(
    tool_parameters_schema(
        command=StringSchema("The shell command to execute"),
        working_dir=StringSchema("Optional working directory for the command"),
        timeout=IntegerSchema(
            60,
            description=(
                "Timeout in seconds. Increase for long-running commands "
                "like compilation or installation (default 60, max 600)."
            ),
            minimum=1,
            maximum=600,
        ),
        capture_file=StringSchema(
            "If set, write command output to this file path as it runs. "
            "The LLM can read this file mid-execution to see partial progress. "
            "Useful for long commands like npm install or compilation."
        ),
        required=["command"],
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
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"(?:^|[;&|]\s*)format\b",       # format (as standalone command only)
            r"\b(mkfs|diskpart)\b",          # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
            # Block writes to nanobot internal state files (#2989).
            # history.jsonl / .dream_cursor are managed by append_history();
            # direct writes corrupt the cursor format and crash /dream.
            r">>?\s*\S*(?:history\.jsonl|\.dream_cursor)",            # > / >> redirect
            r"\btee\b[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",     # tee / tee -a
            r"\b(?:cp|mv)\b(?:\s+[^\s|;&<>]+)+\s+\S*(?:history\.jsonl|\.dream_cursor)",  # cp/mv target
            r"\bdd\b[^|;&<>]*\bof=\S*(?:history\.jsonl|\.dream_cursor)",  # dd of=
            r"\bsed\s+-i[^|;&<>]*(?:history\.jsonl|\.dream_cursor)",  # sed -i
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.allowed_env_keys = allowed_env_keys or []

    @property
    def name(self) -> str:
        return "exec"

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output. "
            "Prefer read_file/write_file/edit_file over cat/echo/sed, "
            "and grep/glob over shell find/grep. "
            "Use -y or --yes flags to avoid interactive prompts. "
            "Output is truncated at 10 000 chars; timeout defaults to 60s."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self, command: str, working_dir: str | None = None,
        timeout: int | None = None, capture_file: str | None = None, **kwargs: Any,
    ) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()

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
                return "Error: working_dir could not be resolved"
            if requested != workspace_root and workspace_root not in requested.parents:
                return "Error: working_dir is outside the configured workspace"

        guard_error = self._guard_command(command, cwd)
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
                capture_path.parent.mkdir(parents=True, exist_ok=True)
                capture_fh = open(capture_path, "w", encoding="utf-8")

            process = await self._spawn(command, cwd, env)
            stdout_chunks: list[bytes] = []
            stderr_lines: list[str] = []

            if capture_fh:
                # Stream lines to file as they arrive
                lines_accum = b""
                try:
                    while True:
                        chunk = await process.stdout.read(512)
                        if not chunk:
                            break
                        lines_accum += chunk
                        while b"\n" in lines_accum:
                            line, lines_accum = lines_accum.split(b"\n", 1)
                            text = line.decode("utf-8", errors="replace")
                            capture_fh.write(text + "\n")
                            capture_fh.flush()
                            stdout_chunks.append(text.encode("utf-8"))
                    if lines_accum:
                        text = lines_accum.decode("utf-8", errors="replace")
                        capture_fh.write(text + "\n")
                        capture_fh.flush()
                        stdout_chunks.append(text.encode("utf-8"))
                except asyncio.TimeoutError:
                    await self._kill_process(process)
                    return f"Error: Command timed out after {effective_timeout} seconds"
                except asyncio.CancelledError:
                    await self._kill_process(process)
                    raise
                # Normal completion: fall through to post-processing
                await process.wait()
                stderr_bytes = await process.stderr.read() if process.stderr else b""
                stderr_text = stderr_bytes.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    stderr_lines.append(stderr_text)
                capture_fh.close()
                stdout_text = b"".join(stdout_chunks).decode("utf-8", errors="replace") if stdout_chunks else ""
                output_parts = [stdout_text] if stdout_text else []
            else:
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        process.communicate(),
                        timeout=effective_timeout,
                    )
                except asyncio.TimeoutError:
                    await self._kill_process(process)
                    return f"Error: Command timed out after {effective_timeout} seconds"
                except asyncio.CancelledError:
                    await self._kill_process(process)
                    raise
                stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
                stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
                output_parts = []
                if stdout_text:
                    output_parts.append(stdout_text)
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            output_parts.append(f"\nExit code: {process.returncode}")

            shell_info = f"[cwd: {cwd}, shell: {'cmd' if _IS_WINDOWS else 'sh'}]"
            result = shell_info + "\n" + ("\n".join(output_parts) if output_parts else "(no output)")

            max_len = self._MAX_OUTPUT
            if len(result) > max_len:
                half = max_len // 2
                result = (
                    result[:half]
                    + f"\n\n... ({len(result) - max_len:,} chars truncated) ...\n\n"
                    + result[-half:]
                )

            return result

        except Exception as e:
            return f"Error executing command: {str(e)}"

    @staticmethod
    def _try_direct_args(command: str) -> list[str] | None:
        """Parse *command* for direct subprocess spawn, bypassing ``cmd.exe /c``.

        Returns ``[exe_path, arg, ...]``, or ``None`` if the command should
        fall through to ``cmd.exe /c`` (shell builtins like ``dir``, ``del``).
        """
        parts = command.strip().split(maxsplit=1)
        exe = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        # If the command contains explicit shell pipe operators (&&, ||),
        # let cmd.exe /c handle the full pipeline — direct spawn would
        # only handle the first invocation and pass shell text to the exe.
        if ('&&' in command) or ('||' in command):
            return None

        # Never try to spawn cmd.exe directly — let _spawn() wrap it in
        # cmd.exe /c as intended.
        if exe.lower() in ("cmd", "cmd.exe"):
            return None

        exe_path = shutil.which(exe)
        if not exe_path:
            return None  # shell builtin — keep cmd.exe /c

        # For known exes with script-taking flags, locate the flag and
        # extract the script text as a single argument (preserving spaces).
        if exe.lower() in _EXES_WITH_SCRIPT_FLAGS and rest:
            # Flag words that take a script/path argument.
            flag_pats = [
                r'(?:^|\s)(-[cC]ommand)(?:\s|$)',
                r'(?:^|\s)(-[fF]ile)(?:\s|$)',
                r'(?:^|\s)(-[eE]ncoded[Cc]ommand)(?:\s|$)',
                r'(?:^|\s)(-[cC])(?:\s|$)',
                r'(?:^|\s)(-[eE])(?:\s|$)',
                r'(?:^|\s)(-[pP])(?:\s|$)',   # node -p (print eval)
            ]
            for pat in flag_pats:
                fm = re.search(pat, rest)
                if not fm:
                    continue
                flag_name = fm.group(1)
                flag_pos = fm.start(1)
                flags_before = rest[:flag_pos].strip()
                script_start = flag_pos + len(flag_name)
                while script_start < len(rest) and rest[script_start].isspace():
                    script_start += 1
                script = rest[script_start:].strip()
                # Detect whether the script is a single "..." or '...' token.
                # If the closing quote is not at the very end, text after it is
                # likely shell syntax (e.g. python -c "..." > output.txt).
                for q in ('"', "'"):
                    if script.startswith(q):
                        close = script.rfind(q)
                        if close == 0:
                            break  # malformed — single quote char
                        if close != len(script) - 1:
                            return None  # trailing shell text
                        script = script[1:-1]
                        break
                if not script:
                    continue  # empty script, try next flag pattern
                args = [exe_path]
                if flags_before:
                    args.extend(flags_before.split())
                args.append(flag_name)
                if script:
                    args.append(script)
                return args
            # No recognised script flag — fall back to cmd.exe /c.
            # Simple split would break on shell metacharacters (>&, etc.)
            # or quoted arguments in the rest.
            return None

        # Generic: stay with cmd.exe /c for unknown exes.
        # Shell semantics (&&, |, >, <, quoting, \" escaping) are
        # fragile to replicate — fall through to avoid regressions.
        return None

    @staticmethod
    async def _spawn(
        command: str, cwd: str, env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """Launch *command* in a platform-appropriate shell."""
        if _IS_WINDOWS:
            # Try direct spawn for executables that exist on PATH
            # (avoids cmd.exe /c mangling embedded double-quotes in -c/-e/... args).
            direct_args = ExecTool._try_direct_args(command)
            if direct_args is not None:
                return await asyncio.create_subprocess_exec(
                    *direct_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
            comspec = env.get("COMSPEC", os.environ.get("COMSPEC", "cmd.exe"))
            return await asyncio.create_subprocess_exec(
                comspec, "/c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        bash = shutil.which("bash") or "/bin/bash"
        return await asyncio.create_subprocess_exec(
            bash, "-l", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        """Kill a subprocess and reap it to prevent zombies."""
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        finally:
            if not _IS_WINDOWS:
                try:
                    os.waitpid(process.pid, os.WNOHANG)
                except (ProcessLookupError, ChildProcessError) as e:
                    logger.debug("Process already reaped or not found: {}", e)

    def _build_env(self) -> dict[str, str]:
        """Build a minimal environment for subprocess execution.

        On Unix, only HOME/LANG/TERM are passed; ``bash -l`` sources the
        user's profile which sets PATH and other essentials.

        On Windows, ``cmd.exe`` has no login-profile mechanism, so a curated
        set of system variables (including PATH) is forwarded.  API keys and
        other secrets are still excluded.
        """
        if _IS_WINDOWS:
            sr = os.environ.get("SYSTEMROOT", r"C:\Windows")
            env = {
                "SYSTEMROOT": sr,
                "COMSPEC": os.environ.get("COMSPEC", f"{sr}\\system32\\cmd.exe"),
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
            return env
        home = os.environ.get("HOME", "/tmp")
        env = {
            "HOME": home,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": os.environ.get("TERM", "dumb"),
        }
        for key in self.allowed_env_keys:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        return env

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        from nanobot.security.network import contains_internal_url
        if contains_internal_url(cmd):
            return "Error: Command blocked by safety guard (internal/private URL detected)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            for raw in self._extract_absolute_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    p = Path(expanded).expanduser().resolve()
                except Exception:
                    continue

                media_path = get_media_dir().resolve()
                if (p.is_absolute()
                    and cwd_path not in p.parents
                    and p != cwd_path
                    and media_path not in p.parents
                    and p != media_path
                ):
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        # Windows: match drive-root paths like `C:\` as well as `C:\path\to\file`
        # NOTE: `*` is required so `C:\` (nothing after the slash) is still extracted.
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]*", command)
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths
