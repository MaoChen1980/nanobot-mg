"""Tests for nanobot.utils.progress_events — progress event helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.utils.progress_events import (
    build_tool_event_finish_payloads,
    build_tool_event_start_payload,
    process_tool_events_and_progress,
    on_progress_accepts_tool_events,
    tool_event_result_extras,
)


class TestOnProgressAcceptsToolEvents:
    def test_cb_with_tool_events_param(self):
        def cb(content, tool_hint=False, tool_events=None):
            pass

        # 3 positional-or-keyword params → accepts tool events
        assert on_progress_accepts_tool_events(cb) is True

    def test_cb_with_4_positional_params(self):
        def cb(content, tool_hint, tool_events, extra):
            pass

        assert on_progress_accepts_tool_events(cb) is True

    def test_cb_without_tool_events_param(self):
        def cb(content, tool_hint=False):
            pass

        assert on_progress_accepts_tool_events(cb) is False

    def test_cb_with_kwargs(self):
        def cb(content, tool_hint=False, **kwargs):
            pass

        assert on_progress_accepts_tool_events(cb) is True

    def test_cb_inspect_error_returns_false(self):
        with patch("nanobot.utils.progress_events.inspect.signature", side_effect=TypeError):
            assert on_progress_accepts_tool_events(lambda: None) is False


class TestToolEventResultExtras:
    def test_non_dict_result(self):
        files, embeds = tool_event_result_extras("string")
        assert files == []
        assert embeds == []

    def test_dict_without_files_or_embeds(self):
        files, embeds = tool_event_result_extras({"result": "ok"})
        assert files == []
        assert embeds == []

    def test_dict_with_files_and_embeds(self):
        result = {"files": ["f1.txt", "f2.txt"], "embeds": [{"url": "https://example.com"}]}
        files, embeds = tool_event_result_extras(result)
        assert files == ["f1.txt", "f2.txt"]
        assert embeds == [{"url": "https://example.com"}]

    def test_files_not_a_list(self):
        result = {"files": "not-a-list", "embeds": None}
        files, embeds = tool_event_result_extras(result)
        assert files == []
        assert embeds == []


class TestBuildToolEventStartPayload:
    def test_uses_tool_call_attributes(self):
        tc = MagicMock()
        tc.id = "call_123"
        tc.name = "read_file_tool"
        tc.arguments = {"path": "/tmp/test.txt"}

        payload = build_tool_event_start_payload(tc)

        assert payload["version"] == 1
        assert payload["phase"] == "start"
        assert payload["call_id"] == "call_123"
        assert payload["name"] == "read_file_tool"
        assert payload["arguments"] == {"path": "/tmp/test.txt"}
        assert payload["result"] is None
        assert payload["error"] is None

    def test_missing_attributes_fallback(self):
        tc = object()

        payload = build_tool_event_start_payload(tc)

        assert payload["call_id"] == ""
        assert payload["name"] == ""
        assert payload["arguments"] == {}


class MockToolCall:
    def __init__(self, id="c1", name="test_tool", arguments=None):
        self.id = id
        self.name = name
        self.arguments = arguments or {}


class TestBuildToolEventFinishPayloads:
    def test_ok_phase(self):
        context = MagicMock()
        context.tool_calls = [MockToolCall(id="c1", name="read_file_tool")]
        context.tool_results = [{"files": ["out.txt"], "embeds": []}]
        context.tool_events = [{"status": "ok"}]

        payloads = build_tool_event_finish_payloads(context)

        assert len(payloads) == 1
        p = payloads[0]
        assert p["phase"] == "end"
        assert p["call_id"] == "c1"
        assert p["name"] == "read_file_tool"
        assert p["result"] == {"files": ["out.txt"], "embeds": []}
        assert p["error"] is None
        assert p["files"] == ["out.txt"]

    def test_error_phase_with_string_result(self):
        context = MagicMock()
        context.tool_calls = [MockToolCall(id="c2", name="exec_tool")]
        context.tool_results = ["Command failed with exit code 1"]
        context.tool_events = [{"status": "error", "detail": "timeout"}]

        payloads = build_tool_event_finish_payloads(context)

        assert len(payloads) == 1
        p = payloads[0]
        assert p["phase"] == "error"
        assert p["result"] is None
        # Error message: detail takes precedence over string result
        assert p["error"] == "timeout"

    def test_error_phase_with_empty_result(self):
        context = MagicMock()
        context.tool_calls = [MockToolCall(id="c3", name="exec_tool")]
        context.tool_results = [""]
        context.tool_events = [{"status": "error", "detail": "timeout"}]

        payloads = build_tool_event_finish_payloads(context)

        assert len(payloads) == 1
        p = payloads[0]
        assert p["error"] == "timeout"

    def test_error_phase_without_detail(self):
        context = MagicMock()
        context.tool_calls = [MockToolCall(id="c4", name="exec_tool")]
        context.tool_results = [""]
        context.tool_events = [{"status": "error"}]

        payloads = build_tool_event_finish_payloads(context)

        assert len(payloads) == 1
        assert payloads[0]["error"] == "Tool execution failed"

    def test_truncates_to_shortest_list(self):
        context = MagicMock()
        context.tool_calls = [MockToolCall(), MockToolCall()]
        context.tool_results = ["ok"]
        context.tool_events = [{"status": "ok"}]

        payloads = build_tool_event_finish_payloads(context)

        assert len(payloads) == 1  # truncated to shortest (results/events)


class TestInvokeOnProgress:
    async def test_with_tool_events_and_accepted(self):
        """When callback accepts tool events (3+ positional), tool_events is forwarded."""
        called_args = []

        async def cb(content, tool_hint=False, tool_events=None):
            called_args.append((content, tool_hint, tool_events))

        await process_tool_events_and_progress(cb, "hello", tool_events=[{"event": "test"}])
        assert called_args == [("hello", False, [{"event": "test"}])]

    async def test_without_tool_events(self):
        cb = AsyncMock()
        await process_tool_events_and_progress(cb, "hello")
        cb.assert_awaited_once_with("hello", tool_hint=False, tool_events=None)

    async def test_tool_events_not_accepted(self):
        cb = AsyncMock()
        with patch("nanobot.utils.progress_events.on_progress_accepts_tool_events", return_value=False):
            await process_tool_events_and_progress(cb, "hello", tool_events=[{"event": "test"}])
        cb.assert_awaited_once_with("hello", tool_hint=False, tool_events=None)

    async def test_with_tool_hint(self):
        cb = AsyncMock()
        await process_tool_events_and_progress(cb, "running exec", tool_hint=True)
        cb.assert_awaited_once_with("running exec", tool_hint=True, tool_events=None)
