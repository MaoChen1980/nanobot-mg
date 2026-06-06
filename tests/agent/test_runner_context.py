from nanobot.agent.runner_context import (
    merge_message_content,
    drop_orphan_tool_results,
    strip_bypassed_tool_messages,
    backfill_missing_tool_results,
)


class TestMergeMessageContent:
    def test_both_strings(self) -> None:
        assert merge_message_content("hello", "world") == "hello\n\nworld"

    def test_left_empty_string(self) -> None:
        assert merge_message_content("", "world") == "world"

    def test_right_empty_string(self) -> None:
        assert merge_message_content("hello", "") == "hello\n\n"

    def test_both_lists(self) -> None:
        left = [{"type": "text", "text": "hello"}]
        right = [{"type": "text", "text": "world"}]
        result = merge_message_content(left, right)
        assert result == [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]

    def test_mixed_left_list_right_string(self) -> None:
        left = [{"type": "text", "text": "hello"}]
        result = merge_message_content(left, "world")
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "hello"}
        assert result[1] == {"type": "text", "text": "world"}

    def test_mixed_left_none_right_list(self) -> None:
        result = merge_message_content(None, [{"type": "text", "text": "hello"}])
        assert result == [{"type": "text", "text": "hello"}]

    def test_none_left_and_right(self) -> None:
        result = merge_message_content(None, None)
        assert result == []


class TestDropOrphanToolResults:
    def test_no_tool_calls(self) -> None:
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        assert drop_orphan_tool_results(msgs) is msgs

    def test_happy_path_assistant_tool_then_result(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "foo"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        result = drop_orphan_tool_results(msgs)
        assert len(result) == 2

    def test_orphan_tool_result_dropped(self) -> None:
        msgs = [
            {"role": "user", "content": "do it"},
            {"role": "tool", "tool_call_id": "orphan_1", "content": "should be dropped"},
        ]
        result = drop_orphan_tool_results(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_duplicate_tool_call_id_stripped(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "foo"}},
                {"id": "call_1", "function": {"name": "foo_dup"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        result = drop_orphan_tool_results(msgs)
        assert len(result) == 2
        assistant_msg = result[0]
        assert len(assistant_msg["tool_calls"]) == 1

    def test_already_fulfilled_tool_call_stripped(self) -> None:
        """tool_call from earlier turn should not be re-declared."""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "foo"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "foo"}}]},
        ]
        result = drop_orphan_tool_results(msgs)
        assert len(result) == 3
        last = result[2]
        assert "tool_calls" not in last or last["tool_calls"] == []

    def test_already_fulfilled_tool_result_dropped(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "foo"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
            {"role": "tool", "tool_call_id": "call_1", "content": "dup result"},
        ]
        result = drop_orphan_tool_results(msgs)
        assert len(result) == 2

    def test_tool_call_without_id_means_result_is_orphan(self) -> None:
        """When assistant tool_call has no id, the subsequent tool result is orphaned."""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "foo"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        result = drop_orphan_tool_results(msgs)
        assert len(result) == 1  # tool result dropped

    def test_tool_call_without_id_kept(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "foo"}}]},
        ]
        result = drop_orphan_tool_results(msgs)
        assert result is msgs

    def test_returns_original_when_no_changes(self) -> None:
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = drop_orphan_tool_results(msgs)
        assert result is msgs

    def test_multiple_fulfilled_cycles(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}, {"id": "b"}]},
            {"role": "tool", "tool_call_id": "a", "content": "r1"},
            {"role": "tool", "tool_call_id": "b", "content": "r2"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}, {"id": "c"}]},
            {"role": "tool", "tool_call_id": "c", "content": "r3"},
        ]
        result = drop_orphan_tool_results(msgs)
        assert len(result) == 5
        assert result[3]["tool_calls"] == [{"id": "c"}]


class TestStripBypassedToolMessages:
    def test_no_bypass_messages(self) -> None:
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert strip_bypassed_tool_messages(msgs) is msgs

    def test_strips_bypassed(self) -> None:
        msgs = [
            {"role": "user", "content": "do it"},
            {"role": "tool", "content": "[BYPASSED] script execution cancelled", "tool_call_id": "x"},
            {"role": "tool", "content": "real result", "tool_call_id": "y"},
        ]
        result = strip_bypassed_tool_messages(msgs)
        assert len(result) == 2

    def test_strips_pending(self) -> None:
        msgs = [
            {"role": "user", "content": "do it"},
            {"role": "tool", "content": "[PENDING] waiting...", "tool_call_id": "x"},
        ]
        result = strip_bypassed_tool_messages(msgs)
        assert len(result) == 1

    def test_keeps_normal_tool_result_with_bypass_word_in_text(self) -> None:
        msgs = [
            {"role": "user", "content": "check status"},
            {"role": "tool", "content": "bypass valve is open", "tool_call_id": "x"},
        ]
        result = strip_bypassed_tool_messages(msgs)
        assert len(result) == 2

    def test_bypassed_detection_is_case_sensitive(self) -> None:
        msgs = [
            {"role": "tool", "content": "[bypassed]", "tool_call_id": "x"},
        ]
        result = strip_bypassed_tool_messages(msgs)
        assert len(result) == 1  # [bypassed] != [BYPASSED]


class TestBackfillMissingToolResults:
    def test_no_missing_results(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "foo"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ]
        assert backfill_missing_tool_results(msgs) is msgs

    def test_backfills_single_missing(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "foo"}}]},
        ]
        result = backfill_missing_tool_results(msgs)
        assert len(result) == 2
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "call_1"
        assert result[1]["name"] == "foo"
        assert "unavailable" in result[1]["content"]

    def test_backfills_multiple_missing(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "function": {"name": "foo"}},
                {"id": "b", "function": {"name": "bar"}},
            ]},
        ]
        result = backfill_missing_tool_results(msgs)
        assert len(result) == 3
        assert result[1]["tool_call_id"] == "a"
        assert result[2]["tool_call_id"] == "b"

    def test_backfill_inserts_after_existing_tool_results(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "function": {"name": "foo"}},
                {"id": "b", "function": {"name": "bar"}},
            ]},
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
        ]
        result = backfill_missing_tool_results(msgs)
        assert len(result) == 3
        assert result[2]["tool_call_id"] == "b"

    def test_mixed_some_fulfilled_some_missing(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "function": {"name": "f1"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "r1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "b", "function": {"name": "f2"}}]},
        ]
        result = backfill_missing_tool_results(msgs)
        assert len(result) == 4
        assert result[3]["tool_call_id"] == "b"

    def test_tool_call_without_id_ignored(self) -> None:
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "foo"}}]},
        ]
        assert backfill_missing_tool_results(msgs) is msgs
