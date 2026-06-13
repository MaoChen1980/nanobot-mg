"""Tests for the restructured MemoryStore — pure file I/O layer."""

import pytest

from nanobot.agent.memory import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


class TestMemoryStoreBasicIO:
    def test_read_memory_returns_empty_when_missing(self, store):
        assert store.read_memory() == ""

    def test_write_and_read_memory(self, store):
        store.write_memory("hello")
        assert store.read_memory() == "hello"

    def test_read_soul_returns_empty_when_missing(self, store):
        assert store.read_soul() == ""

    def test_write_and_read_soul(self, store):
        store.write_soul("soul content")
        assert store.read_soul() == "soul content"

    def test_read_user_returns_empty_when_missing(self, store):
        assert store.read_user() == ""

    def test_write_and_read_user(self, store):
        store.write_user("user content")
        assert store.read_user() == "user content"

    def test_get_memory_context_returns_empty_when_missing(self, store):
        assert store.get_memory_context() == ""

    def test_get_memory_context_returns_formatted_content(self, store):
        store.write_memory("important fact")
        ctx = store.get_memory_context()
        assert "Long-term Memory" in ctx
        assert "important fact" in ctx


class TestMemoryStoreRules:
    def test_read_rules_returns_empty_when_missing(self, store):
        assert store.read_rules() == ""

    def test_write_and_read_rules(self, store):
        store.write_rules("must check builds before commit")
        assert store.read_rules() == "must check builds before commit"

    def test_write_rules_overwrites(self, store):
        store.write_rules("old rule")
        store.write_rules("new rule")
        assert store.read_rules() == "new rule"

    def test_rules_file_property(self, store):
        assert store.rules_file == store.workspace / "RULES.md"

    def test_rules_file_written_to_disk(self, store):
        store.write_rules("no hardcoded secrets")
        assert store.rules_file.exists()
        text = store.rules_file.read_text(encoding="utf-8")
        assert text == "no hardcoded secrets"

    def test_read_rules_via_read_file(self, store):
        store.write_rules("rule from read_file")
        assert store.read_file(store.rules_file) == "rule from read_file"
