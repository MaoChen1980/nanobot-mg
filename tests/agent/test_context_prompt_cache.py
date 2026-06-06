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
    """Workspace with customized MEMORY.md should inject it via build_messages()."""
    from nanobot.utils.gitstore import sync_workspace_templates

    workspace = _make_workspace(tmp_path)
    sync_workspace_templates(workspace, silent=True)

    # Populate memory with customized content (simulating Dream write)
    (workspace / "memory" / "MEMORY.md").write_text(
        "# Long-term Memory\n\nUser prefers dark mode.\n", encoding="utf-8"
    )

    builder = ContextBuilder(workspace)
    messages = builder.build_messages(history=[], current_message="hi")

    static = messages[0]["content"]
    assert "persistent memory" in static
    assert "User prefers dark mode" in static


def test_runtime_context_is_in_system_prompt_not_user_message(tmp_path) -> None:
    """Runtime metadata is in the system prompt; user message is clean."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "Current Time:" in messages[0]["content"]
    assert "Channel: cli" in messages[0]["content"]

    # User message is clean — no runtime context
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert user_content == "Return exactly: OK"
    assert "Current Time:" not in user_content
    assert "Channel: cli" not in user_content



def test_execution_rules_in_system_prompt(tmp_path) -> None:
    """SOUL.md character traits and role definitions should appear in the system prompt."""
    from nanobot.utils.gitstore import sync_workspace_templates

    workspace = _make_workspace(tmp_path)
    sync_workspace_templates(workspace, silent=True)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()
    # Character traits from SOUL.md
    assert "Thorough" in prompt
    assert "framework_search_tool" in prompt  # rules loaded dynamically via framework_search_tool
    # Role definitions from SOUL.md
    assert "Principal engineer" in prompt
    assert "取舍、约束和失效模式" in prompt  # translated from "tradeoffs, constraints, and failure modes"
    # Tool reference from identity.md
    assert "read_file_tool" in prompt


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

    assert "framework_search_tool" in prompt
    assert "memory_search_tool" in prompt



def test_channel_appears_in_runtime_context(tmp_path) -> None:
    """Channel name should appear in runtime context."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[], current_message="hi",
        channel="telegram", chat_id="123",
    )
    system = messages[0]["content"]
    assert "Channel: telegram" in system


def test_channel_absent_when_not_specified(tmp_path) -> None:
    """No channel should not inject Channel: line."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[], current_message="hi",
        channel=None, chat_id="direct",
    )
    system = messages[0]["content"]
    assert "Channel:" not in system


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


def test_system_prompt_template_renders_with_all_variables(tmp_path) -> None:
    """build_system_prompt should render without error when all slots are populated."""
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.agent.tools.search import GrepTool
    from nanobot.utils.gitstore import sync_workspace_templates

    workspace = _make_workspace(tmp_path)
    sync_workspace_templates(workspace, silent=True)

    builder = ContextBuilder(workspace)

    # Build a minimal tool definitions list
    registry = ToolRegistry()
    registry.register(GrepTool(workspace=workspace))
    tool_defs = registry.get_definitions()

    prompt = builder.build_system_prompt(
        channel="cli",
        tool_definitions=tool_defs,
        runtime_context="Current Time: 2026-05-29 12:00 (UTC)",
    )

    assert isinstance(prompt, str)
    assert len(prompt) > 500
    # Core sections should be present
    assert "Environment" in prompt or "OS:" in prompt
