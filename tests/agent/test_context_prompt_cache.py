"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

from datetime import datetime as real_datetime
from importlib.resources import files as pkg_files
from pathlib import Path
import datetime as datetime_module

from nanobot.agent.context import ContextBuilder


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_bootstrap_files_are_backed_by_templates() -> None:
    template_dir = pkg_files("nanobot") / "templates"

    for filename in ContextBuilder.BOOTSTRAP_FILES:
        assert (template_dir / filename).is_file(), f"missing bootstrap template: {filename}"


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because the wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_system_prompt_reflects_current_dream_memory_contract(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "auto-managed" in prompt
    assert "do not edit directly" in prompt


def test_runtime_context_is_in_system_prompt_not_user_message(tmp_path) -> None:
    """Runtime metadata is in the system prompt, user message is clean."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    # Per-turn runtime metadata IS in system prompt now
    assert "Current Time:" in messages[0]["content"]
    assert "Channel: cli" in messages[0]["content"]

    # User message is clean — no runtime context
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert user_content == "Return exactly: OK"
    assert ContextBuilder._RUNTIME_CONTEXT_TAG not in user_content
    assert "Current Time:" not in user_content
    assert "Channel: cli" not in user_content



def test_execution_rules_in_system_prompt(tmp_path) -> None:
    """SOUL.md tag table and core discipline should appear in the system prompt."""
    from nanobot.utils.gitstore import sync_workspace_templates

    workspace = _make_workspace(tmp_path)
    sync_workspace_templates(workspace, silent=True)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()
    # Core discipline from SOUL.md
    assert "Every conclusion needs tool evidence" in prompt
    # Tag table from SOUL.md
    assert "| **#code**" in prompt
    assert "| **#plan**" in prompt
    # Tool reference from identity.md
    assert "read_file" in prompt
    # Task dispatch from identity.md
    assert "直接做" in prompt


def test_identity_has_no_behavioral_instructions(tmp_path) -> None:
    """Identity template should not contain behavioral rules or hardcoded name."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    identity = builder._get_identity(channel=None)
    assert "You are nanobot" not in identity
    assert "Act, don't narrate" not in identity
    assert "Execution Rules" not in identity


def test_framework_search_is_registered(tmp_path) -> None:
    """framework_search tool should be listed in system prompt."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "framework_search" in prompt
    assert "memory_search" in prompt


def test_default_soul_template_contains_execution_rules() -> None:
    """Default SOUL.md template must contain tag dispatch table and core discipline."""
    soul = (pkg_files("nanobot") / "templates" / "SOUL.md").read_text(encoding="utf-8")
    assert "## " in soul  # top-level section exists
    assert "Every conclusion needs tool evidence" in soul  # core discipline
    assert "| **#code**" in soul  # tag table present
    assert "Session Start" in soul  # session start section


def test_channel_format_hint_telegram(tmp_path) -> None:
    """Telegram channel should get messaging-app format hint."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(channel="telegram")
    assert "Format Hint" in prompt
    assert "messaging app" in prompt


def test_channel_format_hint_whatsapp(tmp_path) -> None:
    """WhatsApp should get plain-text format hint."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(channel="whatsapp")
    assert "Format Hint" in prompt
    assert "plain text only" in prompt


def test_channel_format_hint_absent_for_unknown(tmp_path) -> None:
    """Unknown or None channel should not inject a format hint."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(channel=None)
    assert "Format Hint" not in prompt

    prompt2 = builder.build_system_prompt(channel="feishu")
    assert "Format Hint" not in prompt2


def test_build_messages_passes_channel_to_system_prompt(tmp_path) -> None:
    """build_messages should pass channel through to build_system_prompt."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[], current_message="hi",
        channel="telegram", chat_id="123",
    )
    system = messages[0]["content"]
    assert "Format Hint" in system
    assert "messaging app" in system


def test_subagent_result_does_not_create_consecutive_assistant_messages(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[{"role": "assistant", "content": "previous result"}],
        current_message="subagent result",
        channel="cli",
        chat_id="direct",
        current_role="assistant",
    )

    for left, right in zip(messages, messages[1:]):
        assert not (left.get("role") == right.get("role") == "assistant")


def test_always_skills_excluded_from_skills_index(tmp_path) -> None:
    """Skills with always=true appear in Active Skills but NOT in the skills index.

    When no skill has always:true (current state), the Active Skills section
    is absent entirely — which is correct behaviour.
    """
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    # Skills should appear in the summary index
    assert "## Available Skills" in prompt
    # Verify "my" skill is listed in the index (always: false)
    assert "**my**" in prompt

    # Active Skills section: only present when there ARE always:true skills
    always_skills = builder.skills.get_always_skills()
    if always_skills:
        assert "# Active Skills" in prompt
        # those skills appear in Active Skills
        for skill_name in always_skills:
            assert f"### Skill: {skill_name}" in prompt
        # but NOT in the skills index below
        skills_section = prompt.split("## Available Skills\n", 1)
        if len(skills_section) > 1:
            index_text = skills_section[1].split("\n\n---")[0]
            for skill_name in always_skills:
                assert f"**{skill_name}**" not in index_text
    else:
        # No always:true skills → Active Skills section absent
        assert "# Active Skills" not in prompt


def test_template_memory_md_is_skipped(tmp_path) -> None:
    """MEMORY.md matching the bundled template should not inject the Memory section."""
    workspace = _make_workspace(tmp_path)
    from nanobot.utils.gitstore import sync_workspace_templates
    sync_workspace_templates(workspace, silent=True)

    builder = ContextBuilder(workspace)
    messages = builder.build_messages(history=[], current_message="hi")

    dynamic = messages[1]["content"] if len(messages) > 1 else ""
    # Default template MEMORY.md should not inject a memory section.
    assert "=== Memory ===" not in dynamic
    assert "This file is automatically updated by nanobot" not in dynamic


def test_customized_memory_md_is_injected_in_system_prompt(tmp_path) -> None:
    """A Dream-populated MEMORY.md should be injected in the system prompt."""
    workspace = _make_workspace(tmp_path)
    from nanobot.utils.gitstore import sync_workspace_templates
    sync_workspace_templates(workspace, silent=True)

    (workspace / "memory" / "MEMORY.md").write_text(
        "# Long-term Memory\n\nUser prefers dark mode.\n", encoding="utf-8"
    )

    builder = ContextBuilder(workspace)
    messages = builder.build_messages(history=[], current_message="hi")

    static = messages[0]["content"]

    assert "User prefers dark mode" in static
    assert len(messages) == 2  # system + user, no separate dynamic message
