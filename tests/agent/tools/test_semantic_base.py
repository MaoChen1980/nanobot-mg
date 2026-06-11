"""Tests for _semantic_base — _find_representative exception path."""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from nanobot.agent.tools._semantic_base import _find_representative


class TestFindRepresentative:
    """``_find_representative`` — centroid-based sentence selection."""

    def test_normal_flow_returns_sentence(self):
        """When encode succeeds, returns the most representative sentence."""
        model = MagicMock()
        model.encode.return_value = np.array([[0.9, 0.1], [0.1, 0.9], [0.5, 0.5]])
        result = _find_representative("今天天气真好。明天会下雨。后天天晴。", model)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_exception_returns_first_sentence(self):
        """When encode raises, returns the first valid sentence."""
        model = MagicMock()
        model.encode.side_effect = ValueError("encode failed")
        text = "今天天气真不错，适合出去散步。明天可能会下雨，出门记得带伞。后天就会天晴了，温度也很适宜。"
        result = _find_representative(text, model)
        assert result == "今天天气真不错，适合出去散步。"

    def test_short_text_returns_trimmed(self):
        """Text shorter than minimum sentence length returns text[:200]."""
        model = MagicMock()
        result = _find_representative("ab", model)
        assert result == "ab"
        model.encode.assert_not_called()

    def test_no_sentences_returns_truncated_text(self):
        """When no sentences meet min length, returns text[:200]."""
        model = MagicMock()
        text = "a b c d"
        result = _find_representative(text, model)
        assert result == text
        model.encode.assert_not_called()

    def test_exception_with_no_valid_sentences(self):
        """When exception occurs and no valid sentences, returns text[:200]."""
        model = MagicMock()
        model.encode.side_effect = ValueError("encode failed")
        text = "a" * 50
        result = _find_representative(text, model)
        assert result == text[:200]

    def test_sentence_truncated_to_200_chars(self):
        """Each sentence is capped at 200 chars."""
        model = MagicMock()
        model.encode.return_value = np.array([[0.5, 0.5]])
        text = "A" * 300 + "。"
        result = _find_representative(text, model)
        assert len(result) <= 200
