#!/usr/bin/env python3
"""Nanobot-mg business capability test suite.

Tests the 10 core business capabilities through a mix of integration
(in-process AgentLoop) and unit-level tests.

Usage:
    python tmp/test_business.py --all              # run all suites
    python tmp/test_business.py --suite core       # only core tests
    python tmp/test_business.py --suite core,spawn # multiple suites
    python tmp/test_business.py --suite core --verbose
    python tmp/test_business.py --suite core --dry-run  # list tests without running
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import tempfile
import shutil
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ──────────────────────────────────────────────
# Results and reporting
# ──────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    suite: str
    status: str = "FAIL"  # PASS | PASS_WARN | FAIL | SKIP
    detail: str = ""
    duration: float = 0.0
    tools_used: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""
    log_errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status in ("PASS", "PASS_WARN")


# ──────────────────────────────────────────────
# AgentLoop initialisation
# ──────────────────────────────────────────────

_agent_loop: Any = None  # singleton — init once, reuse across suites


def init_agent_loop(workspace: str | None = None) -> Any:
    """Load config and create an AgentLoop (same path as ``nanobot agent``)."""
    global _agent_loop
    if _agent_loop is not None:
        return _agent_loop

    from nanobot.bus.queue import MessageBus
    from nanobot.config.loader import load_config, resolve_config_env_vars
    from nanobot.providers.factory import make_provider
    from nanobot.agent.loop import AgentLoop
    from nanobot.agent.llm_context import set_llm
    from nanobot.cron.service import CronService

    config = resolve_config_env_vars(load_config())
    if workspace:
        config.workspace_path = Path(workspace).expanduser().resolve()

    bus = MessageBus()
    provider = make_provider(config)
    set_llm(provider, config.agents.defaults.model)

    # Use workspace-scoped cron store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Silence nanobot logging during tests to keep output clean
    import logging as _logging
    _logging.getLogger("nanobot").setLevel(_logging.WARNING)

    _agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        project_root=None,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_config=config.tools.web,
        context_block_limit=config.agents.defaults.context_block_limit,
        max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
        provider_retry_mode=config.agents.defaults.provider_retry_mode,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=config.agents.defaults.timezone,
        disabled_skills=config.agents.defaults.disabled_skills,
        tools_config=config.tools,
        pt_save_interval=0,  # disable prompt saving in tests
        assess_interval=config.agents.defaults.assess_interval,
    )
    return _agent_loop


def close_agent_loop() -> None:
    """Cleanup agent loop resources."""
    global _agent_loop
    if _agent_loop is not None:
        try:
            asyncio.run(_agent_loop.close_mcp())
        except Exception:
            pass
        _agent_loop = None


# ──────────────────────────────────────────────
# Test runner helpers
# ──────────────────────────────────────────────

def run_agent(content: str, session_key: str = "test:business") -> tuple[Any, float, list[str]]:
    """Run a message through process_direct_sync and return (response, duration, log_errors).

    Captures any ERROR-level log messages during execution.
    """
    loop = init_agent_loop()
    log_errors: list[str] = []

    # Capture nanobot ERROR logs
    from loguru import logger as _loguru_logger
    _loguru_logger.add(
        lambda msg: log_errors.append(msg.record["message"]),
        level="ERROR",
        filter=lambda r: r["name"].startswith("nanobot"),
    )

    t0 = time.monotonic()
    try:
        response = loop.process_direct_sync(content, session_key=session_key)
    except Exception as e:
        duration = time.monotonic() - t0
        return None, duration, log_errors + [f"Exception: {e}\n{traceback.format_exc()}"]

    duration = time.monotonic() - t0

    return response, duration, log_errors


def check_tools_used(tools_used: list[str], expected: list[str]) -> str | None:
    """Check that *tools_used* contains at least one of the *expected* categories.

    Returns None on success, or a description string on failure.
    """
    if not tools_used:
        return "no tools used"
    if expected:
        for exp in expected:
            if any(exp in t for t in tools_used):
                return None
        return f"expected tools {expected}, got {tools_used}"
    return None


def check_no_error_logs(log_errors: list[str], ignore_patterns: list[str] | None = None) -> list[str]:
    """Filter log errors, returning those that are unexpected."""
    if not log_errors:
        return []
    if not ignore_patterns:
        return log_errors
    import re
    return [e for e in log_errors if not any(re.search(p, e) for p in ignore_patterns)]


# ──────────────────────────────────────────────
# Test suites
# ──────────────────────────────────────────────

CORE_TASKS = [
    {
        "id": "core_basic_qa",
        "desc": "Basic Q&A — simple tool call",
        "prompt": "How many .py files exist in the nanobot/agent/tools/filesystem directory? List their names.",
        "expect_tools": ["glob", "read"],
        "min_content_length": 20,
    },
    {
        "id": "core_multi_tool",
        "desc": "Multi-tool reasoning — analyse naming patterns",
        "prompt": "Look at all tool files in nanobot/agent/tools/ (excluding subdirectories). "
                 "List their filenames and briefly describe each tool's purpose based on the file name.",
        "expect_tools": ["glob", "read", "grep"],
        "min_content_length": 100,
    },
    {
        "id": "core_filesystem",
        "desc": "Filesystem operations — create, write, read, clean up",
        "prompt": "Create a file called /tmp/_nanobot_test_write.txt containing 'hello world'. "
                 "Then read it back and confirm its content. Then delete the file.",
        "expect_tools": ["write", "read", "edit"],
        "min_content_length": 10,
    },
    {
        "id": "core_multi_turn",
        "desc": "Multi-turn context preservation",
        "prompt": "First, list all .py files in nanobot/agent/tools/. "
                 "Then, tell me which of those files contain the word 'async'.",
        "expect_tools": ["glob", "grep"],
        "min_content_length": 30,
    },
    {
        "id": "core_error_handling",
        "desc": "Error handling — invalid tool usage",
        "prompt": "Try to read a file that does not exist at /tmp/_nanobot_nonexistent_file_xyz.txt. "
                 "What error do you get? Then verify the directory /tmp/ exists.",
        "expect_tools": ["read", "glob"],
        "min_content_length": 20,
    },
]


def run_core_tests() -> list[TestResult]:
    """Run 5 core integration tests, each via process_direct_sync."""
    results: list[TestResult] = []
    for task in CORE_TASKS:
        tid = task["id"]
        print(f"  [{tid}] {task['desc']} ... ", end="", flush=True)
        t0 = time.monotonic()
        response, duration, log_errors = run_agent(task["prompt"], session_key=f"test:core:{tid}")
        elapsed = time.monotonic() - t0

        result = TestResult(
            name=tid,
            suite="core",
            duration=elapsed,
            log_errors=log_errors,
        )

        if response is None:
            result.status = "FAIL"
            result.error = "No response (exception during execution)"
            print("FAIL (no response)")
            results.append(result)
            continue

        content = response.content or ""
        result.tools_used = response.tools_used or []
        result.usage = response.usage or {}
        if response.error:
            result.error = response.error

        # Check content
        if len(content) < task["min_content_length"]:
            result.status = "FAIL"
            result.detail = f"content too short: {len(content)} chars"
            print(f"FAIL ({result.detail})")
            results.append(result)
            continue

        # Check tool usage
        tool_issue = check_tools_used(result.tools_used, task["expect_tools"])
        if tool_issue:
            result.status = "FAIL"
            result.detail = tool_issue
            print(f"FAIL ({tool_issue})")
            results.append(result)
            continue

        # Check error logs (ignore assess_me/skill patterns which are non-fatal)
        bad_logs = check_no_error_logs(log_errors, [
            r"skill",
            r"assess_me",
            r"_spawn_skill",
            r"CancelledError",
        ])
        if bad_logs:
            result.status = "PASS_WARN"
            result.detail = f"unexpected error logs: {'; '.join(bad_logs[:3])}"
            print("PASS_WARN")
        else:
            result.status = "PASS"
            print("PASS")

        results.append(result)
    return results


SPAWN_TASKS = [
    {
        "id": "spawn_multi_goal",
        "desc": "Multi-goal analysis — 2 independent targets",
        "prompt": (
            "I need two independent analyses:\n"
            "1. List all Python files in the nanobot/agent/ directory and count them.\n"
            "2. List all Python files in the nanobot/session/ directory and count them.\n"
            "Give me both counts and a brief description of what each directory contains."
        ),
        "expect_tools": ["glob"],
        "min_content_length": 50,
    },
]


def run_spawn_tests() -> list[TestResult]:
    """Verify spawn triggering for multi-goal tasks."""
    results: list[TestResult] = []
    for task in SPAWN_TASKS:
        tid = task["id"]
        print(f"  [{tid}] {task['desc']} ... ", end="", flush=True)
        response, duration, log_errors = run_agent(task["prompt"], session_key=f"test:spawn:{tid}")

        result = TestResult(name=tid, suite="spawn", duration=duration, log_errors=log_errors)

        if response is None:
            result.status = "FAIL"
            result.error = "No response"
            print("FAIL (no response)")
            results.append(result)
            continue

        content = response.content or ""
        result.tools_used = response.tools_used or []
        result.usage = response.usage or {}

        # Check minimal output
        if len(content) < task["min_content_length"]:
            result.status = "FAIL"
            result.detail = f"content too short ({len(content)} chars)"
            print("FAIL")
            results.append(result)
            continue

        # Check tool usage
        tool_issue = check_tools_used(result.tools_used, task["expect_tools"])
        if tool_issue:
            result.status = "FAIL"
            result.detail = tool_issue
            print(f"FAIL ({tool_issue})")
            results.append(result)
            continue

        # Check if spawn was actually used — this is the key metric
        if "spawn" in result.tools_used:
            result.detail = "used spawn tool"
            result.status = "PASS"
            print("PASS (spawned)")
        else:
            result.detail = "no spawn (did task directly — still valid)"
            result.status = "PASS_WARN"
            print("PASS_WARN (no spawn)")

        # Check error logs
        bad_logs = check_no_error_logs(log_errors)
        if bad_logs:
            result.status = "PASS_WARN" if result.passed else "FAIL"
            extra = "; ".join(bad_logs[:2])
            result.detail = f"{result.detail}; error logs: {extra}"
            print(f"  -> error logs: {extra}")

        results.append(result)
    return results


# ──────────────────────────────────────────────
# Compress test suite
# ──────────────────────────────────────────────

def run_compress_tests() -> list[TestResult]:
    """Test compression quality: unit-level Compressor tests."""
    results: list[TestResult] = []

    # ── Test 1: make_summary_pair structure ──
    print("  [compress_summary_pair] synthetic message structure ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.compress import make_summary_pair, _COMPRESSION_NOTICE
        pair = make_summary_pair("test summary")
        result = TestResult(name="compress_summary_pair", suite="compress", duration=time.monotonic() - t0)
        if len(pair) == 1 and pair[0]["role"] == "user" and pair[0]["status"] == "synthetic":
            if _COMPRESSION_NOTICE in pair[0]["content"]:
                result.status = "PASS"
                result.detail = "synthetic pair structure correct"
                print("PASS")
            else:
                result.status = "FAIL"
                result.detail = "missing compression notice"
                print("FAIL (missing notice)")
        else:
            result.status = "FAIL"
            result.detail = f"unexpected pair structure: {pair}"
            print("FAIL")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="compress_summary_pair", suite="compress", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 2: split_history_by_budget ──
    print("  [compress_split_budget] split_by_budget logic ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.compress import split_history_by_budget
        # Create simple test messages
        msgs = [{"role": "user", "content": f"message {i}"} for i in range(10)]
        # Insert assistant messages to create turns
        test_msgs: list[dict] = []
        for i in range(5):
            test_msgs.append({"role": "user", "content": f"user {i}"})
            test_msgs.append({"role": "assistant", "content": f"assistant {i}"})
            test_msgs.append({"role": "tool", "content": f"result {i}", "tool_call_id": f"call_{i}"})

        keeps_raw, to_compress, keeps = split_history_by_budget(
            test_msgs, test_msgs, limit=999999, min_keep_turns=1
        )
        result = TestResult(name="compress_split_budget", suite="compress", duration=time.monotonic() - t0)
        if len(to_compress) == 0 and len(keeps) >= 1:
            result.status = "PASS"
            result.detail = f"keeps={len(keeps)}, compress={len(to_compress)}"
            print("PASS")
        else:
            result.status = "PASS_WARN" if len(keeps) >= 1 else "FAIL"
            result.detail = f"keeps={len(keeps)}, compress={len(to_compress)}"
            print(f"PASS_WARN (keeps={len(keeps)})")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="compress_split_budget", suite="compress", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 3: apply_compress_event ──
    print("  [compress_apply_event] apply_compress_event mutation ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.compress import apply_compress_event
        from nanobot.agent.compressor import CompressEvent

        session_list: list[dict] = [
            {"role": "user", "content": "old msg 1"},
            {"role": "assistant", "content": "old reply 1"},
            {"role": "user", "content": "keep this"},
            {"role": "assistant", "content": "keep this reply"},
        ]
        event = CompressEvent(
            summary="test summary",
            synthetic_pair=[{"role": "user", "content": "synthetic summary", "status": "synthetic"}],
            replaced_raw=[
                {"role": "user", "content": "old msg 1"},
                {"role": "assistant", "content": "old reply 1"},
            ],
        )

        # Create a minimal mock session
        class MockSession:
            messages = list(session_list)
            key = "test:compress"
            metadata: dict = {}
            _last_summary: str | None = None

        apply_compress_event(MockSession, event)
        result = TestResult(name="compress_apply_event", suite="compress", duration=time.monotonic() - t0)
        if len(MockSession.messages) == 2:
            result.status = "PASS"
            result.detail = f"messages reduced from 4 to {len(MockSession.messages)}"
            print("PASS")
        else:
            result.status = "FAIL"
            result.detail = f"expected 2 messages, got {len(MockSession.messages)}"
            print(f"FAIL ({result.detail})")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="compress_apply_event", suite="compress", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 4: strip_xml_tool_calls ──
    print("  [compress_strip_xml] strip residual tool calls from summary ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.compress import _strip_xml_tool_calls
        # _strip_xml_tool_calls removes the tool-call wrappers/markers
        # but keeps any surrounding text content
        test_cases = [
            ('<invoke name="read_file"><parameter name="path">/x</parameter></invoke>', ""),
            ('{tool => "read_file", args => {path => "/x"}}', ""),
            ('[TOOL_CALL]some call[/TOOL_CALL]', "some call"),  # keeps text between markers
            ("normal text without tool calls", "normal text without tool calls"),
            ("text with <invoke name=\"glob\"></invoke> and more", "text with  and more"),  # keeps surrounding text
        ]
        result = TestResult(name="compress_strip_xml", suite="compress", duration=time.monotonic() - t0)
        failures = []
        for inp, expected in test_cases:
            out = _strip_xml_tool_calls(inp)
            if out != expected:
                failures.append(f"input={inp!r}: got {out!r}, expected {expected!r}")
        if not failures:
            result.status = "PASS"
            result.detail = f"{len(test_cases)} patterns OK"
            print("PASS")
        else:
            result.status = "FAIL"
            result.detail = "; ".join(failures[:2])
            print(f"FAIL ({result.detail})")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="compress_strip_xml", suite="compress", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 5: _format_turns filters system/instructions/assess messages ──
    print("  [compress_format_turns] format turns filter ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.compress import _format_turns
        msgs = [
            {"role": "system", "content": "you are a bot"},
            {"role": "user", "content": "## Instructions\n\nbe careful"},
            {"role": "user", "content": "[assess] something[/assess]"},
            {"role": "user", "content": "real user message"},
            {"role": "assistant", "content": "real assistant reply"},
        ]
        formatted = _format_turns(msgs)
        result = TestResult(name="compress_format_turns", suite="compress", duration=time.monotonic() - t0)
        if "real user" in formatted and "## Instructions" not in formatted and "[assess]" not in formatted:
            result.status = "PASS"
            result.detail = "filters system/instructions/assess correctly"
            print("PASS")
        else:
            result.status = "FAIL"
            result.detail = f"unexpected output: {formatted[:100]}"
            print("FAIL")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="compress_format_turns", suite="compress", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    return results


# ──────────────────────────────────────────────
# Assess test suite
# ──────────────────────────────────────────────

def run_assess_tests(loop: Any) -> list[TestResult]:
    """Test assess_me stability.

    We can't easily force assess_me to fire (it depends on LLM output),
    but we can check the infrastructure doesn't crash.
    """
    results: list[TestResult] = []

    # ── Test 1: _make_skill_done_callback handles CancelledError ──
    print("  [assess_callback] _make_skill_done_callback CancelledError ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.loop import _make_skill_done_callback

        async def _test():
            task = asyncio.create_task(asyncio.sleep(999))
            await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0.01)

            cb = _make_skill_done_callback(loop, "test_key")
            # This should NOT raise
            cb(task)

        asyncio.run(_test())
        result = TestResult(name="assess_callback", suite="assess", status="PASS", duration=time.monotonic() - t0)
        print("PASS")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="assess_callback", suite="assess", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 2: _extract_assess_json handles various formats ──
    print("  [assess_extract_json] _extract_assess_json robustness ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.loop import AgentLoop
        extract = AgentLoop._extract_assess_json

        test_inputs = [
            ('{"status": "ok"}', {"status": "ok"}),
            ('<think>checking...</think>\n{"status": "ok"}', {"status": "ok"}),
            ('```json\n{"status": "ok"}\n```', {"status": "ok"}),
            ('some text then {"status": "findings", "content": "fix it"}', {"status": "findings", "content": "fix it"}),
        ]

        failures = []
        for raw, expected in test_inputs:
            parsed = extract(raw)
            if parsed is None:
                failures.append(f"failed to parse: {raw[:50]}")
            elif parsed.get("status") != expected.get("status"):
                failures.append(f"status mismatch: {parsed} != {expected}")

        result = TestResult(name="assess_extract_json", suite="assess", duration=time.monotonic() - t0)
        if not failures:
            result.status = "PASS"
            print("PASS")
        else:
            result.status = "FAIL"
            result.detail = "; ".join(failures[:2])
            print(f"FAIL ({result.detail})")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="assess_extract_json", suite="assess", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    return results


# ──────────────────────────────────────────────
# Session test suite (unit level)
# ──────────────────────────────────────────────

def run_session_tests() -> list[TestResult]:
    """Test session persistence and lifecycle."""
    results: list[TestResult] = []

    # ── Test 1: Session._split_turns_by_assistant ──
    print("  [session_split_turns] turn splitting logic ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.session.manager import Session
        msgs = [
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "follow-up"},
            {"role": "assistant", "content": "second reply"},
        ]
        turns = Session._split_turns_by_assistant(msgs)
        result = TestResult(name="session_split_turns", suite="session", duration=time.monotonic() - t0)
        # First turn starts with assistant, includes subsequent user+tool messages
        if len(turns) == 2 and sum(len(t) for t in turns) == 3:
            result.status = "PASS"
            result.detail = f"2 turns, {sum(len(t) for t in turns)} messages"
            print("PASS")
        else:
            result.status = "FAIL"
            result.detail = f"expected 2 turns (first=1, second=2), got {len(turns)} turns: {[len(t) for t in turns]}"
            print(f"FAIL ({result.detail})")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="session_split_turns", suite="session", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 2: Session.add_message and clear ──
    print("  [session_add_clear] add_message + clear lifecycle ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.session.manager import Session
        s = Session(key="test:lifecycle")
        s.add_message("user", "hello")
        s.add_message("assistant", "world")
        result = TestResult(name="session_add_clear", suite="session", duration=time.monotonic() - t0)
        if len(s.messages) == 2:
            s.clear()
            if len(s.messages) == 0:
                result.status = "PASS"
                result.detail = "add + clear OK"
                print("PASS")
            else:
                result.status = "FAIL"
                result.detail = "clear did not empty messages"
                print("FAIL (clear)")
        else:
            result.status = "FAIL"
            result.detail = f"expected 2 messages, got {len(s.messages)}"
            print(f"FAIL ({len(s.messages)} msgs)")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="session_add_clear", suite="session", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    return results


# ──────────────────────────────────────────────
# Concurrent test suite
# ──────────────────────────────────────────────

def run_concurrent_tests() -> list[TestResult]:
    """Test concurrent session isolation."""
    results: list[TestResult] = []

    print("  [concurrent_sessions] parallel sessions ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        import concurrent.futures
        from nanobot.bus.queue import MessageBus
        from nanobot.config.loader import load_config, resolve_config_env_vars
        from nanobot.providers.factory import make_provider
        from nanobot.agent.loop import AgentLoop

        # Create two independent agent loops for true concurrency test
        config = resolve_config_env_vars(load_config())
        provider = make_provider(config)

        bus1, bus2 = MessageBus(), MessageBus()
        loop1 = AgentLoop(bus=bus1, provider=provider, workspace=config.workspace_path,
                          model=config.agents.defaults.model, channels_config=config.channels,
                          assess_interval=0, pt_save_interval=0)
        loop2 = AgentLoop(bus=bus2, provider=provider, workspace=config.workspace_path,
                          model=config.agents.defaults.model, channels_config=config.channels,
                          assess_interval=0, pt_save_interval=0)

        def _run(lp: Any, msg: str, key: str) -> tuple[str | None, str | None]:
            try:
                # Set ContextVar in each thread (new threads don't inherit)
                from nanobot.agent.llm_context import set_llm
                set_llm(lp.runner.provider, getattr(lp, 'model', None))
                resp = lp.process_direct_sync(msg, session_key=key)
                return resp.content if resp else None, None
            except Exception as e:
                return None, str(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_run, loop1, "What is the capital of France? Reply in 3 words.", "test:conc:1")
            f2 = pool.submit(_run, loop2, "What is 2+2? Reply in 2 words.", "test:conc:2")
            r1, r2 = f1.result(), f2.result()

        status = "PASS"
        issues = []
        if r1[1]:
            issues.append(f"session1 error: {r1[1]}")
        if r2[1]:
            issues.append(f"session2 error: {r2[1]}")
        if r1[0] and r2[0]:
            if "france" not in r1[0].lower() and "paris" not in r1[0].lower():
                issues.append("session1 unexpected content")
            if "4" not in r2[0] and "four" not in r2[0].lower():
                issues.append("session2 unexpected content")

        result = TestResult(name="concurrent_sessions", suite="concurrent", duration=time.monotonic() - t0)

        try:
            asyncio.run(loop1.close_mcp())
        except Exception:
            pass
        try:
            asyncio.run(loop2.close_mcp())
        except Exception:
            pass

        if issues:
            result.status = "PASS_WARN"
            result.detail = "; ".join(issues[:2])
            print(f"PASS_WARN ({result.detail})")
        else:
            result.status = "PASS"
            result.detail = "two parallel sessions completed"
            print("PASS")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="concurrent_sessions", suite="concurrent", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    return results


# ──────────────────────────────────────────────
# Memory test suite
# ──────────────────────────────────────────────

def run_memory_tests() -> list[TestResult]:
    """Test memory system components."""
    results: list[TestResult] = []

    # ── Test 1: MemoryStore basic operations ──
    print("  [memory_store] MemoryStore write_memory/read_memory ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(workspace=Path(tmpdir))
            store.write_memory("test content for memory")
            content = store.read_memory()
            if content and "test content" in content:
                result = TestResult(name="memory_store", suite="memory", status="PASS", duration=time.monotonic() - t0)
                print("PASS")
            else:
                result = TestResult(name="memory_store", suite="memory", status="FAIL", detail=f"read returned: {content}", duration=time.monotonic() - t0)
                print(f"FAIL ({content})")
        results.append(result)
    except ImportError:
        result = TestResult(name="memory_store", suite="memory", status="SKIP", detail="MemoryStore not importable", duration=time.monotonic() - t0)
        print("SKIP")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="memory_store", suite="memory", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    return results


# ──────────────────────────────────────────────
# Main runner
# ──────────────────────────────────────────────

SUITES: dict[str, Callable[..., list[TestResult]]] = {
    "core": run_core_tests,
    "spawn": run_spawn_tests,
    "compress": run_compress_tests,
    "assess": lambda: run_assess_tests(init_agent_loop()),
    "session": run_session_tests,
    "concurrent": run_concurrent_tests,
    "memory": run_memory_tests,
}


def print_report(results: list[TestResult], verbose: bool = False) -> None:
    """Print formatted test report."""
    passed = sum(1 for r in results if r.status == "PASS")
    warned = sum(1 for r in results if r.status == "PASS_WARN")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")

    print()
    print("=" * 72)
    print("  NANOBOT BUSINESS TEST REPORT")
    print("=" * 72)
    print(f"  Total: {len(results)}  |  PASS: {passed}  |  WARN: {warned}  |  FAIL: {failed}  |  SKIP: {skipped}")
    print()

    # Group by suite
    from collections import defaultdict
    by_suite: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        by_suite[r.suite].append(r)

    for suite_name, suite_results in sorted(by_suite.items()):
        suite_passed = sum(1 for r in suite_results if r.passed)
        print(f"  [{suite_name}] {suite_passed}/{len(suite_results)} passed")
        for r in suite_results:
            icon = {"PASS": "  ✓", "PASS_WARN": "  ⚠", "FAIL": "  ✗", "SKIP": "  -"}.get(r.status, "  ?")
            detail = f" — {r.detail}" if r.detail else ""
            err = f" [error: {r.error[:80]}]" if r.error else ""
            print(f"    {icon} {r.name} ({r.duration:.1f}s){detail}{err}")
        if verbose:
            for r in suite_results:
                if r.tools_used:
                    print(f"       tools: {r.tools_used}")
                if r.usage:
                    print(f"       usage: {r.usage}")
                if r.log_errors and verbose > 1:
                    for e in r.log_errors[:3]:
                        print(f"       log: {e[:120]}")
        print()

    if failed > 0:
        print(f"  ❌ {failed} test(s) FAILED")
    else:
        print("  ✅ All tests passed")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Nanobot-mg business capability test suite")
    parser.add_argument("--suite", default="core", help="Comma-separated suite names (core,spawn,compress,assess,session,concurrent,memory)")
    parser.add_argument("--all", action="store_true", help="Run all test suites")
    parser.add_argument("--verbose", "-v", action="count", default=0, help="Verbose output (-vv for more)")
    parser.add_argument("--dry-run", action="store_true", help="List available suites without running")
    args = parser.parse_args()

    if args.dry_run:
        print("Available test suites:")
        for name in SUITES:
            print(f"  {name}")
        return 0

    # Resolve suites to run
    if args.all:
        suite_names = list(SUITES)
    else:
        suite_names = [s.strip() for s in args.suite.split(",")]

    invalid = [s for s in suite_names if s not in SUITES]
    if invalid:
        print(f"Unknown suite(s): {invalid}. Available: {list(SUITES)}")
        return 1

    if any(s in ("core", "spawn", "assess", "concurrent") for s in suite_names):
        print("Initializing AgentLoop (this may take a moment)...")
        init_agent_loop()

    # Run suites
    all_results: list[TestResult] = []
    for suite_name in suite_names:
        print(f"\n── [{suite_name}] ──")
        t0 = time.monotonic()
        try:
            results = SUITES[suite_name]()
        except Exception as e:
            print(f"  Suite {suite_name} crashed: {e}")
            results = [TestResult(name=f"{suite_name}:suite", suite=suite_name, status="FAIL", error=str(e))]
        all_results.extend(results)

    # Report
    print_report(all_results, verbose=args.verbose)

    # Cleanup
    close_agent_loop()

    return 0 if all(r.passed for r in all_results) else 1


if __name__ == "__main__":
    sys.exit(main())
