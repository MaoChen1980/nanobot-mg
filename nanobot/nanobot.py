"""High-level programmatic interface to nanobot."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

from nanobot.agent.hook import AgentHook, SDKCaptureHook
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.utils.compat import dataclass


@dataclass(slots=True)
class RunResult:
    """Result of a single agent run."""

    content: str
    tools_used: list[str]
    messages: list[dict[str, Any]]
    usage: dict[str, int] | None = None
    stop_reason: str | None = None
    error: str | None = None


@dataclass
class StreamEvent:
    """An event yielded by ``Nanobot.stream()``.

    Event types:
        - ``text.delta`` — a text content delta.
        - ``reasoning.delta`` — a reasoning/thinking delta.
        - ``tool.started`` — a tool call started (data is the tool name).
        - ``tool.completed`` — a tool call completed (data is the tool name).
        - ``tool.failed`` — a tool call failed (data is the tool name).
        - ``run.completed`` — the run finished (data is the final content).
        - ``run.failed`` — the run failed (data is the error string).
    """

    type: str
    data: Any


class RunStream:
    """A streaming agent run returned by ``Nanobot.stream()``."""

    def __init__(
        self,
        task: asyncio.Task[Any],
        queue: asyncio.Queue[tuple[str, Any] | None],
        capture: SDKCaptureHook,
    ) -> None:
        self._task = task
        self._queue = queue
        self._capture = capture
        self._consumed = False

    async def stream_events(self) -> AsyncIterator[StreamEvent]:
        """Yield ``StreamEvent`` values as the agent runs.

        Can only be called once per run.  Raises ``RuntimeError`` on re-entry.
        """
        if self._consumed:
            raise RuntimeError("RunStream has already been consumed")
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    break
                typ, data = item
                yield StreamEvent(type=typ, data=data)

            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        finally:
            self._consumed = True

    async def wait(self) -> RunResult:
        """Wait for the run to finish and return a ``RunResult``."""
        if not self._consumed:
            async for _ in self.stream_events():
                pass
        content = self._capture.messages[-1].get("content", "") if self._capture.messages else ""
        if isinstance(content, list):
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content = "\n".join(texts)
        return RunResult(
            content=content,
            tools_used=self._capture.tools_used,
            messages=self._capture.messages,
            usage=self._capture.usage or None,
            stop_reason=self._capture.stop_reason,
            error=self._capture.error,
        )

    async def text(self) -> str:
        """Wait for completion and return the accumulated text content."""
        result = await self.wait()
        return result.content

    def cancel(self) -> None:
        """Cancel the running agent."""
        self._task.cancel()
        self._queue.put_nowait(None)


class Nanobot:
    """Programmatic facade for running the nanobot agent.

    Usage::

        bot = Nanobot.from_config()
        result = await bot.run("Summarize this repo", hooks=[MyHook()])
        print(result.content)
    """

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        *,
        workspace: str | Path | None = None,
    ) -> Nanobot:
        """Create a Nanobot instance from a config file.

        Args:
            config_path: Path to ``config.json``.  Defaults to
                ``~/.nanobot/config.json``.
            workspace: Override the workspace directory from config.
        """
        from nanobot.config.loader import load_config, resolve_config_env_vars
        from nanobot.config.schema import Config

        resolved: Path | None = None
        if config_path is not None:
            resolved = Path(config_path).expanduser().resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"Config not found: {resolved}")

        config: Config = resolve_config_env_vars(load_config(resolved))
        if workspace is not None:
            config.agents.defaults.workspace = str(
                Path(workspace).expanduser().resolve()
            )

        provider = _make_provider(config)
        bus = MessageBus()
        defaults = config.agents.defaults

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=defaults.model,
            max_iterations=defaults.max_tool_iterations,
            context_window_tokens=defaults.context_window_tokens,
            context_block_limit=defaults.context_block_limit,
            max_tool_result_chars=defaults.max_tool_result_chars,
            provider_retry_mode=defaults.provider_retry_mode,
            web_config=config.tools.web,
            exec_config=config.tools.exec,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            mcp_servers=config.tools.mcp_servers,
            timezone=defaults.timezone,
            disabled_skills=defaults.disabled_skills,
            tools_config=config.tools,
            pt_save_interval=defaults.extractor.save_interval,
            assess_interval=defaults.assess_interval,
        )
        return cls(loop)

    async def run(
        self,
        message: str,
        *,
        session_key: str = "sdk:default",
        hooks: list[AgentHook] | None = None,
    ) -> RunResult:
        """Run the agent once and return the result.

        Args:
            message: The user message to process.
            session_key: Session identifier for conversation isolation.
                Different keys get independent history.
            hooks: Optional lifecycle hooks for this run.
        """
        capture = SDKCaptureHook()
        all_hooks = list(hooks) + [capture] if hooks else [capture]
        response = await self._loop.process_direct(
            message, session_key=session_key, extra_hooks=all_hooks,
        )

        content = (response.content if response else None) or ""
        return RunResult(
            content=content,
            tools_used=capture.tools_used,
            messages=capture.messages,
            usage=capture.usage or None,
            stop_reason=capture.stop_reason,
            error=capture.error,
        )

    def stream(
        self,
        message: str,
        *,
        session_key: str = "sdk:default",
        hooks: list[AgentHook] | None = None,
    ) -> RunStream:
        """Run the agent and return a ``RunStream`` for consuming streaming events.

        Usage::

            stream = bot.stream("Hello")
            async for event in stream.stream_events():
                if event.type == "text.delta":
                    print(event.data, end="")
            result = await stream.wait()
        """
        capture = SDKCaptureHook()
        all_hooks = list(hooks) + [capture] if hooks else [capture]
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

        async def _on_stream(delta: str) -> None:
            queue.put_nowait(("text.delta", delta))

        async def _on_stream_end(*args: Any, **kwargs: Any) -> None:
            queue.put_nowait(None)  # sentinel

        task = asyncio.create_task(
            self._loop.process_direct(
                message,
                session_key=session_key,
                on_stream=_on_stream,
                on_stream_end=_on_stream_end,
                extra_hooks=all_hooks,
            )
        )

        return RunStream(task=task, queue=queue, capture=capture)


def _make_provider(config: Any) -> Any:
    """Create the LLM provider from config (extracted from CLI)."""
    from nanobot.providers.factory import make_provider

    return make_provider(config)
