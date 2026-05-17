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

    assert "automatically managed" in prompt
    assert "do not edit directly" in prompt


def test_runtime_context_is_in_user_message_not_system_prompt(tmp_path) -> None:
    """Runtime metadata is prepended to the current user message, not in system prompt."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    # Per-turn runtime metadata should NOT be in system prompt
    assert ContextBuilder._RUNTIME_CONTEXT_END not in messages[0]["content"]
    assert "Channel: cli" not in messages[0]["content"]
    assert "Chat ID: direct" not in messages[0]["content"]

    # Runtime context should be prepended to user message
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert user_content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
    assert "Current Time:" in user_content
    assert "Channel: cli" in user_content
    assert "Chat ID: direct" in user_content
    assert "Return exactly: OK" in user_content
    assert user_content.index(ContextBuilder._RUNTIME_CONTEXT_TAG) < user_content.index("Return exactly: OK")



def test_execution_rules_in_system_prompt(tmp_path) -> None:
    """Execution rules should appear in the system prompt via default SOUL.md."""
    from nanobot.utils.gitstore import sync_workspace_templates

    workspace = _make_workspace(tmp_path)
    sync_workspace_templates(workspace, silent=True)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()
    # Template uses Chinese WHEN-THEN structure; check for key semantic phrases
    assert "直接执行" in prompt  # simple tasks → direct execution
    assert "先给大纲" in prompt  # complex tasks → outline first
    assert "read_file" in prompt  # read before write
    assert "stdout" in prompt or "stderr" in prompt or "验证" in prompt  # verify result


def test_identity_has_no_behavioral_instructions(tmp_path) -> None:
    """Identity template should not contain behavioral rules or hardcoded name."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    identity = builder._get_identity(channel=None)
    assert "You are nanobot" not in identity
    assert "Act, don't narrate" not in identity
    assert "Execution Rules" not in identity


def test_agents_framework_architecture_in_bootstrap_docs(tmp_path) -> None:
    """Framework Architecture section appears in the AGENTS.md bootstrap docs."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "stateless per turn" in prompt


def test_default_soul_template_contains_execution_rules() -> None:
    """Default SOUL.md template must contain execution rules with act/plan layering."""
    soul = (pkg_files("nanobot") / "templates" / "SOUL.md").read_text(encoding="utf-8")
    assert "## " in soul  # top-level section exists
    assert "直接执行" in soul  # simple tasks → direct execution
    assert "先给大纲" in soul  # complex tasks → outline first
    assert "read_file" in soul  # read before write


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


def test_customized_memory_md_is_injected(tmp_path) -> None:
    """A Dream-populated MEMORY.md should be injected in the dynamic system message."""
    workspace = _make_workspace(tmp_path)
    from nanobot.utils.gitstore import sync_workspace_templates
    sync_workspace_templates(workspace, silent=True)

    (workspace / "memory" / "MEMORY.md").write_text(
        "# Long-term Memory\n\nUser prefers dark mode.\n", encoding="utf-8"
    )

    builder = ContextBuilder(workspace)
    messages = builder.build_messages(history=[], current_message="hi")

    static = messages[0]["content"]
    dynamic = messages[1]["content"] if len(messages) > 1 else ""

    assert "User prefers dark mode" not in static
    assert "User prefers dark mode" in dynamic
