from types import SimpleNamespace

from nanobot.agent.loop_utils import strip_think, runtime_chat_id


class TestStripThink:
    def test_none(self) -> None:
        assert strip_think(None) is None

    def test_empty_string(self) -> None:
        assert strip_think("") == ""

    def test_no_think_tag(self) -> None:
        assert strip_think("hello world") == "hello world"

    def test_think_block(self) -> None:
        result = strip_think("<think>some reasoning</think>output")
        assert result == "output"

    def test_think_block_only(self) -> None:
        result = strip_think("<think>reasoning</think>")
        assert result == ""

    def test_unclosed_think(self) -> None:
        result = strip_think("<think>unclosed reasoning")
        assert result == ""

    def test_unclosed_think_with_trailing_text(self) -> None:
        result = strip_think("<think>unclosed\noutput after")
        assert result == ""  # entire input consumed by ^\s*<think>[\s\S]*$ pattern

    def test_thought_block(self) -> None:
        result = strip_think("<thought>reasoning</thought>output")
        assert result == "output"

    def test_unclosed_thought(self) -> None:
        result = strip_think("<thought>unclosed")
        assert result == ""

    def test_malformed_think_no_closing_gt(self) -> None:
        """<thinkmalformed is NOT stripped — <think followed by alphanumeric is a different tag."""
        result = strip_think("<thinkmalformed")
        assert result == "<thinkmalformed"

    def test_orphan_close_think_at_start(self) -> None:
        result = strip_think("</think>output")
        assert result == "output"

    def test_orphan_close_think_at_end(self) -> None:
        result = strip_think("output</think>")
        assert result == "output"

    def test_orphan_close_thought_at_start(self) -> None:
        result = strip_think("</thought>output")
        assert result == "output"

    def test_channel_marker(self) -> None:
        result = strip_think("<channel|>output")
        assert result == "output"

    def test_channel_marker_pipe(self) -> None:
        result = strip_think("<|channel|>output")
        assert result == "output"

    def test_multiple_think_blocks(self) -> None:
        result = strip_think("<think>first</think>middle<think>second</think>end")
        assert result == "middleend"

    def test_text_and_think_mixed(self) -> None:
        result = strip_think("before<think>during</think>after")
        assert result == "beforeafter"

    def test_think_tag_with_attributes_not_stripped(self) -> None:
        result = strip_think("<thinkpad>is a brand</thinkpad>")
        assert result == "<thinkpad>is a brand</thinkpad>"


class TestRuntimeChatId:
    def test_uses_metadata_context_chat_id(self) -> None:
        msg = SimpleNamespace(metadata={"context_chat_id": "ctx-123"}, chat_id="chat-456")
        assert runtime_chat_id(msg) == "ctx-123"

    def test_falls_back_to_chat_id(self) -> None:
        msg = SimpleNamespace(metadata={}, chat_id="chat-456")
        assert runtime_chat_id(msg) == "chat-456"

