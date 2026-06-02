from nanobot.session.manager import Session


def _assert_no_orphans(history: list[dict]) -> None:
    """Assert every tool result in history has a matching assistant tool_call."""
    declared = {
        tc["id"]
        for m in history if m.get("role") == "assistant"
        for tc in (m.get("tool_calls") or [])
    }
    orphans = [
        m.get("tool_call_id") for m in history
        if m.get("role") == "tool" and m.get("tool_call_id") not in declared
    ]
    assert orphans == [], f"orphan tool_call_ids: {orphans}"


def _tool_turn(prefix: str, idx: int) -> list[dict]:
    """Helper: one assistant with 2 tool_calls + 2 tool results."""
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": f"{prefix}_{idx}_a", "type": "function", "function": {"name": "x", "arguments": "{}"}},
                {"id": f"{prefix}_{idx}_b", "type": "function", "function": {"name": "y", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": f"{prefix}_{idx}_a", "name": "x", "content": "ok"},
        {"role": "tool", "tool_call_id": f"{prefix}_{idx}_b", "name": "y", "content": "ok"},
    ]


def test_format_history_drops_orphan_tool_results_when_window_cuts_tool_calls():
    session = Session(key="telegram:test")
    session.messages.append({"role": "user", "content": "old turn"})
    for i in range(20):
        session.messages.extend(_tool_turn("old", i))
    session.messages.append({"role": "user", "content": "problem turn"})
    for i in range(25):
        session.messages.extend(_tool_turn("cur", i))
    session.messages.append({"role": "user", "content": "new telegram question"})

    history = session.format_history()
    _assert_no_orphans(history)


def test_legitimate_tool_pairs_preserved():
    session = Session(key="test:positive")
    session.messages.append({"role": "user", "content": "hello"})
    for i in range(5):
        session.messages.extend(_tool_turn("ok", i))
    session.messages.append({"role": "assistant", "content": "done"})

    history = session.format_history()
    _assert_no_orphans(history)
    tool_ids = [m["tool_call_id"] for m in history if m.get("role") == "tool"]
    assert len(tool_ids) == 10
    assert history[0]["role"] == "user"


def test_orphan_trim_with_last_consolidated():
    """Orphan trimming works correctly when session is partially consolidated."""
    session = Session(key="test:consolidated")
    for i in range(10):
        session.messages.append({"role": "user", "content": f"old {i}"})
        session.messages.extend(_tool_turn("cons", i))
    session.last_consolidated = 30

    session.messages.append({"role": "user", "content": "recent"})
    for i in range(15):
        session.messages.extend(_tool_turn("new", i))
    session.messages.append({"role": "user", "content": "latest"})

    history = session.format_history()
    _assert_no_orphans(history)


def test_no_tool_messages_unchanged():
    session = Session(key="test:plain")
    for i in range(5):
        session.messages.append({"role": "user", "content": f"q{i}"})
        session.messages.append({"role": "assistant", "content": f"a{i}"})

    history = session.format_history()
    assert len(history) == 10
    _assert_no_orphans(history)


def test_all_orphan_prefix_stripped():
    session = Session(key="test:all-orphan")
    session.messages.append({"role": "tool", "tool_call_id": "gone_1", "name": "x", "content": "ok"})
    session.messages.append({"role": "tool", "tool_call_id": "gone_2", "name": "y", "content": "ok"})
    session.messages.append({"role": "user", "content": "fresh start"})
    session.messages.append({"role": "assistant", "content": "hi"})

    history = session.format_history()
    _assert_no_orphans(history)
    assert history[0]["role"] == "user"
    assert len(history) == 2


def test_empty_session_history():
    session = Session(key="test:empty")
    history = session.format_history()
    assert history == []


def test_format_history_preserves_reasoning_content():
    session = Session(key="test:reasoning")
    session.messages.append({"role": "user", "content": "hi"})
    session.messages.append({
        "role": "assistant",
        "content": "done",
        "reasoning_content": "hidden chain of thought",
    })

    history = session.format_history()

    assert history == [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "done",
            "reasoning_content": "hidden chain of thought",
        },
    ]


def test_format_history_annotates_all_message_types_with_timestamps():
    session = Session(key="test:timestamps")
    session.messages.append({
        "role": "user",
        "content": "10 点提醒是昨天发生的",
        "timestamp": "2026-04-26T22:00:00",
    })
    session.messages.append({
        "role": "assistant",
        "content": "记下来了",
        "timestamp": "2026-04-26T22:00:05",
    })

    history = session.format_history(include_timestamps=True)

    assert history == [
        {
            "role": "user",
            "content": "10 点提醒是昨天发生的",
            "timestamp": "2026-04-26 22:00:00 UTC",
        },
        {
            "role": "assistant",
            "content": "记下来了",
            "timestamp": "2026-04-26 22:00:05 UTC",
        },
    ]


def test_format_history_annotates_proactive_assistant_deliveries_with_timestamps():
    session = Session(key="test:proactive-timestamps")
    session.messages.append({
        "role": "assistant",
        "content": "记得喝水",
        "timestamp": "2026-04-26T15:00:00",
        "_channel_delivery": True,
    })
    session.messages.append({
        "role": "user",
        "content": "好",
        "timestamp": "2026-04-26T18:00:00",
    })

    history = session.format_history(include_timestamps=True)

    assert history == [
        {
            "role": "assistant",
            "content": "记得喝水",
            "timestamp": "2026-04-26 15:00:00 UTC",
        },
        {
            "role": "user",
            "content": "好",
            "timestamp": "2026-04-26 18:00:00 UTC",
        },
    ]


def test_format_history_annotates_tool_results_with_timestamps():
    session = Session(key="test:tool-timestamps")
    session.messages.append({"role": "user", "content": "run tool"})
    session.messages.extend(_tool_turn("ts", 0))
    session.messages[-1]["timestamp"] = "2026-04-26T22:00:10"

    history = session.format_history(include_timestamps=True)

    tool_result = history[-1]
    assert tool_result["role"] == "tool"
    assert tool_result["content"] == "ok"
    assert tool_result["timestamp"] == "2026-04-26 22:00:10 UTC"


def test_window_starts_in_mid_tool_group():
    """Format history handles messages with tool groups that start mid-group."""
    session = Session(key="test:mid-cut")
    session.messages.append({"role": "user", "content": "setup"})
    session.messages.append({
        "role": "assistant", "content": None,
        "tool_calls": [
            {"id": "split_a", "type": "function", "function": {"name": "x", "arguments": "{}"}},
            {"id": "split_b", "type": "function", "function": {"name": "y", "arguments": "{}"}},
        ],
    })
    session.messages.append({"role": "tool", "tool_call_id": "split_a", "name": "x", "content": "ok"})
    session.messages.append({"role": "tool", "tool_call_id": "split_b", "name": "y", "content": "ok"})
    session.messages.append({"role": "user", "content": "next"})
    session.messages.extend(_tool_turn("intact", 0))
    session.messages.append({"role": "assistant", "content": "final"})

    history = session.format_history()
    _assert_no_orphans(history)


def test_format_history_synthesizes_image_breadcrumb_from_media_kwarg():
    session = Session(key="test:media")
    session.messages.append(
        {"role": "user", "content": "look", "media": ["/m/a.png", "/m/b.png"]}
    )
    session.messages.append({"role": "assistant", "content": "nice"})

    history = session.format_history()

    assert history == [
        {"role": "user", "content": "look\n[image: /m/a.png]\n[image: /m/b.png]"},
        {"role": "assistant", "content": "nice"},
    ]


def test_format_history_synthesizes_breadcrumb_for_image_only_turn():
    session = Session(key="test:image-only")
    session.messages.append({"role": "user", "content": "", "media": ["/m/pic.png"]})
    session.messages.append({"role": "assistant", "content": "I see a cat"})

    history = session.format_history()

    assert history[0] == {"role": "user", "content": "[image: /m/pic.png]"}


def test_format_history_ignores_media_kwarg_on_non_user_rows():
    session = Session(key="test:defensive")
    session.messages.append(
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "structured"}],
            "media": ["/m/x.png"],
        }
    )
    history = session.format_history()
    assert history[0]["content"] == [{"type": "text", "text": "structured"}]
