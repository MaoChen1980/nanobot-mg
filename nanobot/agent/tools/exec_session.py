"""Session support for long-running exec workflows."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, build_parameters_schema

DEFAULT_YIELD_MS = 1000
MAX_YIELD_MS = 30_000
DEFAULT_MAX_OUTPUT_CHARS = 10_000
MAX_OUTPUT_CHARS = 32_000
MAX_STDIN_CHARS = 500_000
MAX_BUFFERED_CHARS = 200_000


@dataclass(slots=True)
class _SessionPoll:
    output: str
    done: bool
    exit_code: int | None
    elapsed_s: float = 0.0
    timed_out: bool = False
    terminated: bool = False
    stdin_closed: bool = False
    truncated_chars: int = 0
    raw_output: str = ""
    ctx_tail: str = ""


@dataclass(slots=True)
class ExecSessionInfo:
    session_id: str
    command: str
    cwd: str
    elapsed_s: float
    idle_s: float
    remaining_s: float
    returncode: int | None
    owner_session_key: str | None = None


_IS_WINDOWS = os.name == "nt"


class _WinptyProcess:
    """Duck-type compatible replacement for asyncio.subprocess.Process using pywinpty."""

    def __init__(self, args: list[str], cwd: str, env: dict[str, str]) -> None:
        from winpty import PtyProcess

        self._pty = PtyProcess.spawn(args, cwd=cwd, env=env)
        self.returncode: int | None = None
        self.pid: int = self._pty.pid
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def kill(self) -> None:
        try:
            self._pty.terminate()
            self._pty.close()
        except Exception:
            pass
        self.returncode = 0

    async def wait(self) -> int:
        while self._pty.isalive():
            await asyncio.sleep(0.1)
        self._pty.close()
        self.returncode = 0
        return 0


class _ExecSession:
    def __init__(
        self,
        *,
        session_id: str,
        process: asyncio.subprocess.Process,
        command: str,
        cwd: str,
        timeout: int | None,
        owner_session_key: str | None = None,
        master_fd: int | None = None,
    ) -> None:
        self.session_id = session_id
        self.process = process
        self.command = command
        self.cwd = cwd
        self.owner_session_key = owner_session_key
        self.started_at = time.monotonic()
        self.deadline = time.monotonic() + timeout if timeout else float("inf")
        self.last_access = time.monotonic()
        self._chunks: list[str] = []
        self._lock = asyncio.Lock()
        self._timed_out = False
        self._history_buf: str = ""  # rolling history of all output, capped at MAX_BUFFERED_CHARS
        self._pty_master: int | None = None
        self._winpty_proc: Any | None = None
        self._pty_task: asyncio.Task | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

        if isinstance(process, _WinptyProcess):
            self._winpty_proc = process._pty
            self._pty_task = asyncio.create_task(self._read_pty_win())
        elif master_fd is not None:
            os.set_blocking(master_fd, False)
            self._pty_master = master_fd
            self._pty_task = asyncio.create_task(self._read_pty())
        else:
            self._stdout_task = asyncio.create_task(self._read_stream(process.stdout, ""))
            self._stderr_task = asyncio.create_task(self._read_stream(process.stderr, "STDERR:\n"))

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        prefix: str,
    ) -> None:
        if stream is None:
            return
        first = True
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            if prefix and first:
                text = prefix + text
                first = False
            async with self._lock:
                current = sum(len(c) for c in self._chunks)
                if current >= MAX_BUFFERED_CHARS:
                    continue
                if current + len(text) > MAX_BUFFERED_CHARS:
                    text = text[: MAX_BUFFERED_CHARS - current]
                self._chunks.append(text)

    async def _read_pty(self) -> None:
        """Read from PTY master fd in executor thread."""
        loop = asyncio.get_event_loop()
        while self._pty_master is not None:
            try:
                chunk = await loop.run_in_executor(None, os.read, self._pty_master, 65536)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                async with self._lock:
                    current = sum(len(c) for c in self._chunks)
                    if current >= MAX_BUFFERED_CHARS:
                        continue
                    if current + len(text) > MAX_BUFFERED_CHARS:
                        text = text[: MAX_BUFFERED_CHARS - current]
                    self._chunks.append(text)
            except BlockingIOError:
                await asyncio.sleep(0.05)
            except OSError:
                break

    async def _read_pty_win(self) -> None:
        """Read from pywinpty PTY in executor thread."""
        loop = asyncio.get_event_loop()
        while self._winpty_proc is not None:
            try:
                text: str = await loop.run_in_executor(None, self._winpty_proc.read)
                if not text:
                    break
                async with self._lock:
                    current = sum(len(c) for c in self._chunks)
                    if current >= MAX_BUFFERED_CHARS:
                        continue
                    if current + len(text) > MAX_BUFFERED_CHARS:
                        text = text[: MAX_BUFFERED_CHARS - current]
                    self._chunks.append(text)
            except EOFError:
                break

    async def write(self, chars: str) -> str | None:
        if self._winpty_proc is not None:
            self._winpty_proc.write(chars)
            return None
        if self.process.returncode is not None:
            return "session has already exited"
        if self._pty_master is not None:
            os.write(self._pty_master, chars.encode("utf-8"))
            return None
        if self.process.stdin is None:
            return "session stdin is not available"
        try:
            self.process.stdin.write(chars.encode("utf-8"))
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return "session stdin is closed"
        return None

    async def close_stdin(self) -> str | None:
        if self._winpty_proc is not None:
            return None  # no way to close stdin on winpty independently
        if self._pty_master is not None:
            return None  # PTY has no stdin to close; close pty to signal EOF
        if self.process.returncode is not None:
            return "session has already exited"
        if self.process.stdin is None:
            return "session stdin is not available"
        self.process.stdin.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await self.process.stdin.wait_closed()
        return None

    async def poll(
        self,
        yield_time_ms: int,
        max_output_chars: int,
        *,
        terminated: bool = False,
        stdin_closed: bool = False,
    ) -> _SessionPoll:
        self.last_access = time.monotonic()
        if yield_time_ms > 0 and self.process.returncode is None:
            await asyncio.sleep(min(yield_time_ms, MAX_YIELD_MS) / 1000)

        # Poll winpty process exit status
        if self._winpty_proc is not None and not self._winpty_proc.isalive():
            self.process.returncode = 0

        if self.process.returncode is None and time.monotonic() >= self.deadline:
            self._timed_out = True
            await self.kill()

        if self.process.returncode is not None:
            with suppress(asyncio.TimeoutError):
                if self._pty_task is not None:
                    await asyncio.wait_for(self._pty_task, timeout=2.0)
                else:
                    await asyncio.wait_for(
                        asyncio.gather(self._stdout_task, self._stderr_task),
                        timeout=2.0,
                    )

        async with self._lock:
            raw = "".join(self._chunks)
            self._chunks.clear()

        if raw:
            self._history_buf += raw
            if len(self._history_buf) > MAX_BUFFERED_CHARS:
                self._history_buf = self._history_buf[-MAX_BUFFERED_CHARS:]

        output, truncated = _truncate_output(raw, max_output_chars)

        ctx_tail = ""
        if not output and self._history_buf:
            # No new output — include tail of history for context
            ctx_tail = self._history_buf[-500:]
        return _SessionPoll(
            output=output,
            raw_output=raw if truncated else "",
            done=self.process.returncode is not None,
            exit_code=self.process.returncode,
            elapsed_s=max(0.0, time.monotonic() - self.started_at),
            timed_out=self._timed_out,
            terminated=terminated,
            stdin_closed=stdin_closed,
            truncated_chars=truncated,
            ctx_tail=ctx_tail,
        )

    async def kill(self) -> None:
        if self._winpty_proc is not None:
            try:
                self._winpty_proc.terminate()
                self._winpty_proc.close()
            except Exception:
                pass
            self._winpty_proc = None
            self.process.returncode = 0
            return
        if self._pty_master is not None:
            try:
                os.close(self._pty_master)
            except OSError:
                pass
            self._pty_master = None
        if self.process.returncode is not None:
            return
        self.process.kill()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.process.wait(), timeout=5.0)


class ExecSessionManager:
    def __init__(self, *, max_sessions: int = 8, idle_timeout: int = 1800) -> None:
        self.max_sessions = max_sessions
        self.idle_timeout = idle_timeout
        self._sessions: dict[str, _ExecSession] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        command: str,
        cwd: str,
        env: dict[str, str],
        timeout: int | None,
        shell_program: str | None,
        login: bool,
        yield_time_ms: int,
        max_output_chars: int,
        owner_session_key: str | None = None,
        use_pty: bool = False,
    ) -> tuple[str, _SessionPoll]:
        async with self._lock:
            await self._cleanup_locked()
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(f"maximum exec sessions reached ({self.max_sessions})")
            process, master_fd = await self._spawn(command, cwd, env, shell_program, login, use_pty=use_pty)
            session_id = uuid.uuid4().hex[:12]
            session = _ExecSession(
                session_id=session_id,
                process=process,
                command=command,
                cwd=cwd,
                timeout=timeout,
                owner_session_key=owner_session_key,
                master_fd=master_fd,
            )
            self._sessions[session_id] = session

        poll = await session.poll(yield_time_ms, max_output_chars)
        if poll.done:
            async with self._lock:
                self._sessions.pop(session_id, None)
        return session_id, poll

    async def write(
        self,
        *,
        session_id: str,
        chars: str | None,
        close_stdin: bool,
        terminate: bool,
        yield_time_ms: int,
        max_output_chars: int,
        owner_session_key: str | None = None,
    ) -> _SessionPoll:
        async with self._lock:
            await self._cleanup_locked()
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if (
            owner_session_key
            and session.owner_session_key
            and session.owner_session_key != owner_session_key
        ):
            raise KeyError(session_id)

        if chars:
            error = await session.write(chars)
            if error:
                raise RuntimeError(error)
        stdin_closed = False
        if close_stdin:
            error = await session.close_stdin()
            if error:
                raise RuntimeError(error)
            stdin_closed = True
        if terminate:
            await session.kill()
        poll = await session.poll(
            yield_time_ms,
            max_output_chars,
            terminated=terminate,
            stdin_closed=stdin_closed,
        )
        if poll.done:
            async with self._lock:
                self._sessions.pop(session_id, None)
        return poll

    async def list(self, *, owner_session_key: str | None = None) -> list[ExecSessionInfo]:
        async with self._lock:
            await self._cleanup_locked()
            now = time.monotonic()
            return [
                ExecSessionInfo(
                    session_id=session_id,
                    command=session.command,
                    cwd=session.cwd,
                    elapsed_s=max(0.0, now - session.started_at),
                    idle_s=max(0.0, now - session.last_access),
                    remaining_s=max(0.0, session.deadline - now),
                    returncode=session.process.returncode,
                    owner_session_key=session.owner_session_key,
                )
                for session_id, session in sorted(self._sessions.items())
                if not owner_session_key
                or not session.owner_session_key
                or session.owner_session_key == owner_session_key
            ]

    async def _cleanup_locked(self) -> None:
        now = time.monotonic()
        stale = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_access > self.idle_timeout
        ]
        for session_id in stale:
            session = self._sessions.pop(session_id)
            await session.kill()

    async def _spawn(
        self,
        command: str,
        cwd: str,
        env: dict[str, str],
        shell_program: str | None,
        login: bool,
        *,
        use_pty: bool = False,
    ) -> tuple[asyncio.subprocess.Process, int | None]:
        if use_pty and not _IS_WINDOWS:
            import pty
            shell = shell_program or shutil.which("bash") or "/bin/bash"
            args = [shell]
            if login:
                args.append("-l")
            args.extend(["-c", command])
            master_fd, slave_fd = pty.openpty()
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                cwd=cwd, env=env,
            )
            os.close(slave_fd)
            return proc, master_fd

        if use_pty and _IS_WINDOWS:
            shell = shell_program or "powershell.exe"
            is_pwsh = "powershell" in shell.lower() or "pwsh" in shell.lower()
            if is_pwsh:
                args = [shell, "-Command", command]
            else:
                args = [shell]
                if login:
                    args.append("-l")
                args.extend(["-c", command])
            proc = _WinptyProcess(args, cwd, env)
            return proc, None

        from nanobot.agent.tools.shell import ExecTool

        proc = await ExecTool._spawn(
            command, cwd, env, shell_program, login,
            stdin=asyncio.subprocess.PIPE,
        )
        return proc, None


DEFAULT_EXEC_SESSION_MANAGER = ExecSessionManager()


def clamp_session_int(value: int | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    return min(max(value, minimum), maximum)


def _truncate_output(output: str, max_output_chars: int) -> tuple[str, int]:
    if len(output) <= max_output_chars:
        return output, 0
    half = max_output_chars // 2
    omitted = len(output) - max_output_chars
    return (
        output[:half]
        + f"\n\n... ({omitted:,} chars truncated) ...\n\n"
        + output[-half:],
        omitted,
    )


def format_session_poll(session_id: str, poll: _SessionPoll) -> str:
    parts = [poll.output] if poll.output else []
    if not poll.output and poll.ctx_tail:
        parts.append(f"[Context tail (last {len(poll.ctx_tail)} chars)]\n{poll.ctx_tail}")
    if poll.truncated_chars:
        parts.append(f"(output truncated by {poll.truncated_chars:,} chars)")
    if poll.timed_out:
        parts.append("Error: Command timed out; session was terminated.")
    if poll.terminated and not poll.timed_out:
        parts.append("Session terminated.")
    if poll.stdin_closed:
        parts.append("Stdin closed.")
    if poll.done:
        parts.append(f"Exit code: {poll.exit_code}")
    else:
        parts.append(f"Process running. session_id: {session_id}")
    parts.append(f"Elapsed: {poll.elapsed_s:.1f}s")
    return "\n".join(parts) if parts else "(no output yet)"


@tool_parameters(build_parameters_schema(
    session_id=p("string", "Session id returned by exec when yield_time_ms is used."),
    chars=p("string", "Bytes/text to write to stdin. Omit or pass an empty string to only poll recent output.", nullable=True),
    close_stdin=p("boolean", "Close stdin after writing chars. Useful for commands waiting for EOF.", default=False),
    terminate=p("boolean", "Terminate the running exec session.", default=False),
    yield_time_ms=p("integer", "Milliseconds to wait before returning recent output (default 1000, max 30000).", minimum=0, maximum=MAX_YIELD_MS),
    max_output_chars=p("integer", "Maximum output characters to return from this poll (default 10000, max 32000).", minimum=1000, maximum=MAX_OUTPUT_CHARS),
    max_output_tokens=p("integer", "Compatibility alias for max_output_chars. The current runtime uses a character budget.", minimum=1000, maximum=MAX_OUTPUT_CHARS, nullable=True),
    required=["session_id"],
))
class WriteStdinTool(Tool):
    """Write to or poll a running exec session."""

    name = "write_stdin"

    description = (
        "**Purpose**: Send stdin to or poll output of a running exec session.\n\n"
        "**When to use**:\n"
        "- Send input to a running command (passwords, yes/no responses, etc.)\n"
        "- Poll for recent output\n"
        "- Close stdin (send EOF) or terminate a long-running session\n\n"
        "**Usage**:\n"
        "- chars='' or omit → poll output only, no stdin write\n"
        "- chars='text\\n' → send text to stdin\n"
        "- close_stdin=true → close stdin (send EOF)\n"
        "- terminate=true → kill the process\n\n"
        "**Interactive prompts**: Just write chars and the process receives them. "
        "Check the returned output to see what the process responded with. "
        "If the expected prompt text is not in the output yet, call write_stdin again with yield_time_ms to wait for more output."
    )

    exclusive = True

    def __init__(
        self,
        *,
        manager: ExecSessionManager | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        self._manager = manager or DEFAULT_EXEC_SESSION_MANAGER
        self._output_dir = Path(output_dir) if output_dir else Path.cwd()

    async def _persist_output(self, session_id: str, output: str) -> str:
        ts = time.strftime("%Y%m%d-%H%M%S")
        filename = f"exec_{session_id}_{ts}.txt"
        out_dir = self._output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        path.write_text(output, encoding="utf-8")
        return str(path)

    async def execute(
        self,
        session_id: str,
        chars: str | None = None,
        close_stdin: bool = False,
        terminate: bool = False,
        yield_time_ms: int | None = None,
        max_output_chars: int | None = None,
        max_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            if chars and len(chars) > MAX_STDIN_CHARS:
                return f"Error: stdin input too long ({len(chars):,} chars, max {MAX_STDIN_CHARS:,})"
            if max_output_chars is None:
                max_output_chars = max_output_tokens
            output_limit = clamp_session_int(
                max_output_chars,
                DEFAULT_MAX_OUTPUT_CHARS,
                1000,
                MAX_OUTPUT_CHARS,
            )
            poll = await self._manager.write(
                session_id=session_id,
                chars=chars,
                close_stdin=close_stdin,
                terminate=terminate,
                yield_time_ms=clamp_session_int(yield_time_ms, DEFAULT_YIELD_MS, 0, MAX_YIELD_MS),
                max_output_chars=output_limit,
            )
            result = format_session_poll(session_id, poll)
            if poll.raw_output:
                fpath = await self._persist_output(session_id, poll.raw_output)
                result += f"\nFull output ({poll.truncated_chars:,} extra chars) saved to: {fpath}\nUse read_file if you need the full output."
            return result
        except KeyError:
            return f"Error: exec session not found: {session_id}"
        except Exception as exc:
            return f"Error writing to exec session: {exc}"



@tool_parameters(build_parameters_schema())
class ListInteractTool(Tool):
    """List active interact sessions."""

    name = "list_interact"

    description = (
        "**Purpose**: List active interact sessions (session_id, cwd, elapsed, remaining timeout, command preview).\n\n"
        "**When to use**:\n"
        "- Recover a lost session_id after context shift\n"
        "- Check which interact sessions are running\n"
        "- Before continuing with write_stdin after conversation was interrupted"
    )

    read_only = True

    def __init__(self, *, manager: ExecSessionManager | None = None) -> None:
        self._manager = manager or DEFAULT_EXEC_SESSION_MANAGER

    async def execute(self, **kwargs: Any) -> str:
        try:
            sessions = await self._manager.list()
            if not sessions:
                return "No active exec sessions."
            lines = []
            for info in sessions:
                command = " ".join(info.command.split())
                if len(command) > 120:
                    command = command[:119] + "..."
                status = "exited" if info.returncode is not None else "running"
                lines.append(
                    f"{info.session_id} | {status} | elapsed={info.elapsed_s:.1f}s "
                    f"| idle={info.idle_s:.1f}s | remaining={info.remaining_s:.1f}s "
                    f"| cwd={info.cwd} | {command}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error listing exec sessions: {exc}"


@tool_parameters(build_parameters_schema(
    command=p("string", "Command to run in the interactive session (e.g., ssh user@host)."),
    use_pty=p("boolean",
        "Use a PTY for interactive prompts. Required for SSH password prompts. "
        "Default: true.",
        default=True,
    ),
    timeout=p("integer",
        "Session timeout in seconds (default 3600). The session is killed after this.",
        minimum=30, maximum=7200, nullable=True,
    ),
    yield_time_ms=p("integer",
        "Milliseconds to wait for initial output before returning (default 5000, max 30000).",
        minimum=1000, maximum=MAX_YIELD_MS, nullable=True,
    ),
    max_output_chars=p("integer",
        "Maximum output characters returned (default 10000, max 32000).",
        minimum=1000, maximum=MAX_OUTPUT_CHARS, nullable=True,
    ),
    required=["command"],
))
class InteractTool(Tool):
    """Create a long-running interactive session."""

    name = "interact"

    description = (
        "**Purpose**: Start an interactive session with a PTY (SSH, telnet, etc.).\n\n"
        "**When to use** instead of **exec**:\n"
        "- SSH into a remote server (SSH needs a PTY for password prompts)\n"
        "- Any command that prompts for passwords, yes/no, or interactive input\n"
        "- Long-running commands where you want to send input dynamically\n\n"
        "**Workflow**:\n"
        "1. **interact**(command=\"ssh user@host\")\n"
        "   → Returns a session_id and initial output (e.g., password prompt)\n"
        "2. **write_stdin**(session_id=..., chars=\"password\\n\")\n"
        "   → Sends input, returns output (e.g., shell prompt)\n"
        "3. **write_stdin**(session_id=..., chars=\"command\\n\")\n"
        "   → Keep sending commands\n"
        "4. **write_stdin**(session_id=..., terminate=True)\n"
        "   → End the session\n\n"
        "**Tip**: Always uses a PTY, so SSH password prompts work. "
        "If you don't see expected output yet, call write_stdin with yield_time_ms to wait longer."
    )

    exclusive = True

    def __init__(
        self,
        *,
        manager: ExecSessionManager | None = None,
    ) -> None:
        self._manager = manager or DEFAULT_EXEC_SESSION_MANAGER

    async def execute(
        self,
        command: str,
        use_pty: bool = True,
        timeout: int | None = None,
        yield_time_ms: int | None = None,
        max_output_chars: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            effective_timeout = min(timeout or 3600, 7200)
            session_id, poll = await self._manager.start(
                command=command,
                cwd=os.getcwd(),
                env=os.environ,
                timeout=effective_timeout,
                shell_program=None,
                login=True,
                yield_time_ms=clamp_session_int(yield_time_ms or 5000, 5000, 1000, MAX_YIELD_MS),
                max_output_chars=clamp_session_int(
                    max_output_chars, DEFAULT_MAX_OUTPUT_CHARS, 1000, MAX_OUTPUT_CHARS,
                ),
                use_pty=use_pty,
            )
            return format_session_poll(session_id, poll)
        except Exception as exc:
            return f"Error starting session: {exc}"
