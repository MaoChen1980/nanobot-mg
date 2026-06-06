import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop, _SessionDispatchState
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.session.manager import Session


def _mk_loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    from nanobot.config.schema import AgentDefaults

    loop.max_tool_result_chars = AgentDefaults().max_tool_result_chars
    return loop


def _make_full_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


def test_record_turn_skips_multimodal_user_when_only_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:runtime-only")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._append_turn_to_session(
        session,
        [{"role": "user", "content": [{"type": "text", "text": runtime}]}],
        skip=0,
    )
    assert session.messages == []


def test_record_turn_keeps_image_placeholder_with_path_after_runtime_strip() -> None:
    loop = _mk_loop()
    session = Session(key="test:image")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._append_turn_to_session(
        session,
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": runtime},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}, "_meta": {"path": "/media/feishu/photo.jpg"}},
            ],
        }],
        skip=0,
    )
    assert session.messages[0]["content"] == [{"type": "text", "text": "[image: /media/feishu/photo.jpg]"}]


def test_record_turn_keeps_image_placeholder_without_meta() -> None:
    loop = _mk_loop()
    session = Session(key="test:image-no-meta")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._append_turn_to_session(
        session,
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": runtime},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }],
        skip=0,
    )
    assert session.messages[0]["content"] == [{"type": "text", "text": "[image]"}]


def test_record_turn_keeps_tool_results_under_16k() -> None:
    loop = _mk_loop()
    session = Session(key="test:tool-result")
    content = "x" * 12_000

    loop._append_turn_to_session(
        session,
        [{"role": "tool", "tool_call_id": "call_1", "name": "read_file_tool", "content": content}],
        skip=0,
    )

    assert session.messages[0]["content"] == content


def test_restore_runtime_checkpoint_rehydrates_completed_and_pending_tools() -> None:
    loop = _mk_loop()
    session = Session(
        key="test:checkpoint",
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file_tool", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "exec_tool", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file_tool",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec_tool", "arguments": "{}"},
                    }
                ],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is None
    assert session.messages[0]["role"] == "assistant"
    assert session.messages[1]["tool_call_id"] == "call_done"
    assert session.messages[2]["tool_call_id"] == "call_pending"
    assert "interrupted before this tool finished" in session.messages[2]["content"].lower()


def test_restore_runtime_checkpoint_dedupes_overlapping_tail() -> None:
    loop = _mk_loop()
    session = Session(
        key="test:checkpoint-overlap",
        messages=[
            {
                "role": "assistant",
                "content": "working",
                "tool_calls": [
                    {
                        "id": "call_done",
                        "type": "function",
                        "function": {"name": "read_file_tool", "arguments": "{}"},
                    },
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec_tool", "arguments": "{}"},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_done",
                "name": "read_file_tool",
                "content": "ok",
            },
        ],
        metadata={
            AgentLoop._RUNTIME_CHECKPOINT_KEY: {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file_tool", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "exec_tool", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file_tool",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec_tool", "arguments": "{}"},
                    }
                ],
            }
        },
    )

    restored = loop._restore_runtime_checkpoint(session)

    assert restored is True
    assert session.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is None
    assert len(session.messages) == 3
    assert session.messages[0]["role"] == "assistant"
    assert session.messages[1]["tool_call_id"] == "call_done"
    assert session.messages[2]["tool_call_id"] == "call_pending"


@pytest.mark.asyncio
async def test_process_message_persists_user_message_before_turn_completes(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop._run_agent_loop = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c1", content="persist me")
    with pytest.raises(RuntimeError, match="boom"):
        await loop._process_message(msg)

    persisted = loop.sessions.get_or_create("feishu:c1")
    assert [m["role"] for m in persisted.messages] == ["user"]
    assert persisted.messages[0]["content"] == "persist me"
    assert persisted.metadata.get(AgentLoop._PENDING_USER_TURN_KEY) is True
    assert persisted.updated_at >= persisted.created_at


# 1x1 PNG used by the media-persistence tests. ``separate_and_extract_media`` runs
# at the top of ``_process_message`` and filters ``msg.media`` down to
# paths that magic-byte-sniff as images, so the test fixture needs real
# bytes on disk (not just placeholder paths).






@pytest.mark.asyncio
async def test_process_message_does_not_duplicate_early_persisted_user_message(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop._run_agent_loop = AsyncMock(return_value=(
        "done",
        None,
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "done"},
        ],
        "stop",
        False,
    ))  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(channel="feishu", sender_id="u1", chat_id="c2", content="hello")
    )

    assert result is not None
    assert result.content == "done"
    session = loop.sessions.get_or_create("feishu:c2")
    assert [
        {k: v for k, v in m.items() if k in {"role", "content"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "done"},
    ]
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata


@pytest.mark.asyncio
async def test_process_message_uses_context_chat_id_for_runtime_prompt(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.context.build_messages = MagicMock(  # type: ignore[method-assign]
        return_value=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "runtime + hello"},
        ]
    )
    loop._run_agent_loop = AsyncMock(return_value=(  # type: ignore[method-assign]
        "done",
        [],
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "runtime + hello"},
            {"role": "assistant", "content": "done"},
        ],
        "stop",
        False,
    ))

    result = await loop._process_message(
        InboundMessage(
            channel="discord",
            sender_id="u1",
            chat_id="thread-777",
            content="hello",
            metadata={"context_chat_id": "parent-456"},
            session_key_override="discord:parent-456:thread:thread-777",
        )
    )

    assert result is not None
    assert result.chat_id == "thread-777"
    assert loop.context.build_messages.call_args.kwargs["chat_id"] == "parent-456"
    assert loop._run_agent_loop.call_args.kwargs["chat_id"] == "thread-777"


def test_set_tool_context_uses_effective_key_for_spawn_tool(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    spawn_tool = loop.tools.get("spawn_tool")
    assert spawn_tool is not None

    loop._set_tool_context(
        "discord",
        "thread-777",
        session_key="discord:parent-456:thread:thread-777",
    )

    assert spawn_tool._origin_channel.get() == "discord"  # type: ignore[attr-defined]
    assert spawn_tool._origin_chat_id.get() == "thread-777"  # type: ignore[attr-defined]
    assert spawn_tool._session_key.get() == "discord:parent-456:thread:thread-777"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_next_turn_after_crash_closes_pending_user_turn_before_new_input(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)
    loop.provider.chat_with_retry = AsyncMock(return_value=MagicMock())  # unused because _run_agent_loop is stubbed

    session = loop.sessions.get_or_create("feishu:c3")
    session.add_message("user", "old question")
    session.metadata[AgentLoop._PENDING_USER_TURN_KEY] = True
    loop.sessions.save(session)

    loop._run_agent_loop = AsyncMock(return_value=(
        "new answer",
        None,
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "Error: Task interrupted before a response was generated."},
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
        ],
        "stop",
        False,
    ))  # type: ignore[method-assign]

    result = await loop._process_message(
        InboundMessage(channel="feishu", sender_id="u1", chat_id="c3", content="new question")
    )

    assert result is not None
    assert result.content == "new answer"
    session = loop.sessions.get_or_create("feishu:c3")
    assert [
        {k: v for k, v in m.items() if k in {"role", "content"}}
        for m in session.messages
    ] == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "Error: Task interrupted before a response was generated."},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata


@pytest.mark.asyncio
async def test_stop_preserves_runtime_checkpoint_for_next_turn(tmp_path: Path) -> None:
    from nanobot.command.builtin import cmd_stop
    from nanobot.command.router import CommandContext

    loop = _make_full_loop(tmp_path)

    checkpoint_saved = asyncio.Event()

    async def interrupted_run_agent_loop(_initial_messages, *, session=None, **_kwargs):
        assert session is not None
        loop._set_runtime_checkpoint(
            session,
            {
                "assistant_message": {
                    "role": "assistant",
                    "content": "working",
                    "tool_calls": [
                        {
                            "id": "call_done",
                            "type": "function",
                            "function": {"name": "read_file_tool", "arguments": "{}"},
                        },
                        {
                            "id": "call_pending",
                            "type": "function",
                            "function": {"name": "exec_tool", "arguments": "{}"},
                        },
                    ],
                },
                "completed_tool_results": [
                    {
                        "role": "tool",
                        "tool_call_id": "call_done",
                        "name": "read_file_tool",
                        "content": "ok",
                    }
                ],
                "pending_tool_calls": [
                    {
                        "id": "call_pending",
                        "type": "function",
                        "function": {"name": "exec_tool", "arguments": "{}"},
                    }
                ],
            },
        )
        checkpoint_saved.set()
        await asyncio.Event().wait()

    loop._run_agent_loop = interrupted_run_agent_loop  # type: ignore[method-assign]

    first_msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c4", content="keep progress")
    task = asyncio.create_task(loop._process_message(first_msg))
    loop._session_dispatch[first_msg.session_key] = _SessionDispatchState(tasks=[task], pending=asyncio.Queue())
    await asyncio.wait_for(checkpoint_saved.wait(), timeout=1.0)

    stop_msg = InboundMessage(channel="feishu", sender_id="u1", chat_id="c4", content="/stop")
    stop_ctx = CommandContext(msg=stop_msg, session=None, key=stop_msg.session_key, raw="/stop", loop=loop)
    stop_result = await cmd_stop(stop_ctx)

    assert "Stopped 1 task" in stop_result.content
    assert task.done()

    interrupted = loop.sessions.get_or_create("feishu:c4")
    assert interrupted.metadata.get(AgentLoop._PENDING_USER_TURN_KEY) is True
    assert interrupted.metadata.get(AgentLoop._RUNTIME_CHECKPOINT_KEY) is not None

    async def resumed_run_agent_loop(initial_messages, **_kwargs):
        return (
            "next answer",
            None,
            [*initial_messages, {"role": "assistant", "content": "next answer"}],
            "stop",
            False,
        )

    loop._run_agent_loop = resumed_run_agent_loop  # type: ignore[method-assign]
    result = await loop._process_message(
        InboundMessage(channel="feishu", sender_id="u1", chat_id="c4", content="continue here")
    )

    assert result is not None
    assert result.content == "next answer"

    session = loop.sessions.get_or_create("feishu:c4")
    msgs = [
        {k: v for k, v in m.items() if k in {"role", "content", "tool_call_id", "name"}}
        for m in session.messages
    ]
    assert msgs[0] == {"role": "user", "content": "keep progress"}
    assert msgs[1] == {"role": "assistant", "content": "working"}
    assert msgs[2] == {"role": "tool", "tool_call_id": "call_done", "name": "read_file_tool", "content": "ok"}
    assert msgs[3]["role"] == "tool"
    assert msgs[3]["tool_call_id"] == "call_pending"
    assert msgs[3]["name"] == "exec_tool"
    assert msgs[3]["content"].startswith("====== Message Time:")
    assert msgs[3]["content"].endswith("Error: Task interrupted before this tool finished.")
    assert msgs[4] == {"role": "user", "content": "continue here"}
    assert msgs[5] == {"role": "assistant", "content": "next answer"}
    assert AgentLoop._PENDING_USER_TURN_KEY not in session.metadata
    assert AgentLoop._RUNTIME_CHECKPOINT_KEY not in session.metadata



def test_prompt_merge_does_not_replace_standalone_subagent_history_entry(tmp_path: Path) -> None:
    loop = _mk_loop()
    session = Session(key="cli:merge")
    session.add_message("assistant", "previous assistant")

    inserted = loop._persist_subagent_followup(
        session,
        InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id="cli:merge",
            content="subagent result",
            metadata={"subagent_task_id": "sub-1"},
        ),
    )

    assert inserted is True

    builder = ContextBuilder(tmp_path)
    projected = builder.build_messages(
        history=session.format_history(),
        current_message="",
        current_role="assistant",
        channel="cli",
        chat_id="merge",
    )

    non_system = [m for m in projected if m.get("role") != "system"]
    assert len(non_system) == 2
    assert "subagent result" in non_system[-1]["content"]
    assert session.messages[-1]["content"] == "subagent result"
    assert session.messages[-1]["injected_event"] == "subagent_result"


def test_subagent_followup_dedupes_by_task_id() -> None:
    loop = _mk_loop()
    session = Session(key="cli:dedupe")
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:dedupe",
        content="subagent result",
        metadata={"subagent_task_id": "sub-1"},
    )

    assert loop._persist_subagent_followup(session, msg) is True
    assert loop._persist_subagent_followup(session, msg) is False
    assert len(session.messages) == 1


def test_subagent_followup_skips_empty_content() -> None:
    loop = _mk_loop()
    session = Session(key="cli:empty")
    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:empty",
        content="",
        metadata={"subagent_task_id": "sub-empty"},
    )

    assert loop._persist_subagent_followup(session, msg) is False
    assert session.messages == []


def test_set_tool_context_passes_thread_session_key_to_spawn(tmp_path: Path) -> None:
    loop = _make_full_loop(tmp_path)

    loop._set_tool_context(
        "slack",
        "C123",
        metadata={"slack": {"thread_ts": "1700.42", "channel_type": "channel"}},
        session_key="slack:C123:1700.42",
    )

    spawn_tool = loop.tools.get("spawn_tool")
    assert spawn_tool is not None
    assert spawn_tool._session_key.get() == "slack:C123:1700.42"


class TestRecoveryManagerContract:
    """RecoveryManager only touches session metadata — never calls sessions.save()."""

    def test_set_runtime_checkpoint_does_not_save(self):
        loop = MagicMock()
        from nanobot.agent.loop_checkpoint import RecoveryManager
        rm = RecoveryManager(loop)
        session = MagicMock()

        rm.set_runtime_checkpoint(session, {"key": "val"})

        loop.sessions.save.assert_not_called()

    def test_clear_runtime_checkpoint_does_not_save(self):
        loop = MagicMock()
        from nanobot.agent.loop_checkpoint import RecoveryManager
        rm = RecoveryManager(loop)
        session = MagicMock()
        session.metadata = {"runtime_checkpoint": {}}

        rm.clear_runtime_checkpoint(session)

        loop.sessions.save.assert_not_called()

    def test_mark_pending_user_turn_does_not_save(self):
        loop = MagicMock()
        from nanobot.agent.loop_checkpoint import RecoveryManager
        rm = RecoveryManager(loop)
        session = MagicMock()

        rm.mark_pending_user_turn(session)

        loop.sessions.save.assert_not_called()

    def test_restore_runtime_checkpoint_does_not_save(self):
        loop = MagicMock()
        from nanobot.agent.loop_checkpoint import RecoveryManager
        rm = RecoveryManager(loop)
        session = MagicMock()
        session.metadata = {}
        session.messages = []

        rm.restore_and_clear_checkpoint(session)

        loop.sessions.save.assert_not_called()
