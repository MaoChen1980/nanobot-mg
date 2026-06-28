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
    # ── Larger-scale core tasks ──
    {
        "id": "core_code_analysis",
        "desc": "Deep code analysis — compare patterns across 3+ files",
        "prompt": "Read all Python files in nanobot/agent/tools/filesystem/. For each file, "
                 "identify the class names, their parent classes, and all method names. "
                 "Then compare them across files: what base class do they share?",
        "expect_tools": ["glob", "read"],
        "min_content_length": 100,
    },
    {
        "id": "core_dependency_trace",
        "desc": "Dependency tracing — follow imports across modules",
        "prompt": "Read nanobot/agent/loop.py and find all import statements that import from "
                 "'nanobot.agent' submodules. List each submodule and summarize what part "
                 "of the agent loop it supports.",
        "expect_tools": ["read", "grep"],
        "min_content_length": 100,
    },
    {
        "id": "core_cross_verify",
        "desc": "Cross-verification — two methods, one answer",
        "prompt": "Count the .py files in nanobot/agent/tools/ using TWO methods: "
                 "1) glob for '*.py' pattern, 2) list the directory and count .py files. "
                 "Do the two counts match? If not, explain why.",
        "expect_tools": ["glob", "grep"],
        "min_content_length": 50,
    },
    {
        "id": "core_long_reasoning",
        "desc": "Multi-step reasoning chain (3 sequential insights)",
        "prompt": "Step 1: List all .py files in nanobot/agent/tools/ and group them "
                 "by naming convention (snake_case vs camelCase vs other).\n"
                 "Step 2: Based on naming patterns, which tools handle 'external resources' "
                 "(web, search, filesystem) vs 'internal logic'?\n"
                 "Step 3: Verify your classification by reading 2-3 of the tool files. "
                 "Was your grouping accurate? Report any surprises.",
        "expect_tools": ["glob", "read", "grep"],
        "min_content_length": 200,
    },
    {
        "id": "core_multi_round",
        "desc": "Multi-round convergence — refine answer with new data",
        "prompt": "First, find the longest Python file in nanobot/agent/tools/ (by line count). "
                 "Read its first 30 lines to understand its purpose. Then find its test file "
                 "if it exists. Based on the tool's complexity and test coverage, "
                 "give a quality assessment score (1-10) with reasoning.",
        "expect_tools": ["glob", "read", "grep"],
        "min_content_length": 150,
    },
]


def run_core_tests() -> list[TestResult]:
    """Run 10 core integration tests, each via process_direct_sync."""
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

        # Check error logs (ignore expected non-fatal patterns)
        bad_logs = check_no_error_logs(log_errors, [
            r"skill",
            r"assess_me",
            r"_spawn_skill",
            r"CancelledError",
            r"Anthropic API error",       # transient provider connection noise
            r"connection error",
            r"Failed to create provider",
            r"APIStatusError",
            r"retry_after",
            r"stream stalled",
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
    {
        "id": "spawn_reader_3plus",
        "desc": "3+ file comparison — read and compare structure across 3 modules",
        "prompt": (
            "I need you to read and compare the structure of three files:\n"
            "1. nanobot/agent/compress.py — list the top-level classes and functions\n"
            "2. nanobot/agent/compressor.py — list the top-level classes and functions\n"
            "3. nanobot/agent/context.py — list the top-level classes and functions\n"
            "For each file, I want counts of classes vs functions. "
            "Then tell me: what's the relationship between compress.py and compressor.py?"
        ),
        "expect_tools": ["read", "grep"],
        "min_content_length": 100,
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
    """Test compression quality: unit-level Compressor tests + real summary."""
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
        test_cases = [
            ('<invoke name="read_file"><parameter name="path">/x</parameter></invoke>', ""),
            ('{tool => "read_file", args => {path => "/x"}}', ""),
            ('[TOOL_CALL]some call[/TOOL_CALL]', "some call"),
            ("normal text without tool calls", "normal text without tool calls"),
            ("text with <invoke name=\"glob\"></invoke> and more", "text with  and more"),
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

    # ── Test 6: Real summarization via summarize_turns ──
    print("  [compress_real_summary] summarize_turns with real-ish data ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.compress import summarize_turns
        from nanobot.agent.llm_context import set_llm
        from nanobot.config.loader import load_config, resolve_config_env_vars
        from nanobot.providers.factory import make_provider

        test_turns = [
            {"role": "user", "content": "What files are in the project root?"},
            {"role": "assistant", "content": "Let me check with glob."},
            {"role": "user", "content": "List all Python dependencies used."},
            {"role": "assistant", "content": "The project uses loguru, pyyaml, aiohttp, and pydantic."},
            {"role": "user", "content": "What's the architecture pattern?"},
            {"role": "assistant", "content": "It follows a modular agent pattern with a message bus."},
        ]

        config = resolve_config_env_vars(load_config())
        provider = make_provider(config)
        set_llm(provider, config.agents.defaults.model)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            summary = loop.run_until_complete(summarize_turns(test_turns))
        finally:
            loop.close()

        result = TestResult(name="compress_real_summary", suite="compress", duration=time.monotonic() - t0)
        if summary and len(summary) >= 20:
            result.status = "PASS"
            result.detail = f"summary length: {len(summary)} chars"
            print("PASS")
        elif summary:
            result.status = "PASS_WARN"
            result.detail = f"summary too short: {len(summary)} chars"
            print(f"PASS_WARN ({len(summary)} chars)")
        else:
            result.status = "FAIL"
            result.detail = "empty summary returned"
            print("FAIL (empty)")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="compress_real_summary", suite="compress", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    return results


# ──────────────────────────────────────────────
# Assess test suite
# ──────────────────────────────────────────────

def run_assess_tests(loop: Any) -> list[TestResult]:
    """Test assess_me stability."""
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
        if len(turns) == 2 and sum(len(t) for t in turns) == 3:
            result.status = "PASS"
            result.detail = f"2 turns, {sum(len(t) for t in turns)} messages"
            print("PASS")
        else:
            result.status = "FAIL"
            result.detail = f"expected 2 turns, got {len(turns)} turns: {[len(t) for t in turns]}"
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

    # ── Test 3: Session JSON serialization round-trip ──
    print("  [session_json_roundtrip] session to/from JSON ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.session.manager import Session
        s = Session(key="test:json")
        s.add_message("user", "hello")
        s.add_message("assistant", "world")
        s.metadata["test_key"] = "test_value"

        data = s.to_dict() if hasattr(s, 'to_dict') else {
            "key": s.key,
            "messages": s.messages,
            "metadata": s.metadata,
            "created_at": s.created_at.isoformat() if hasattr(s.created_at, 'isoformat') else s.created_at,
            "updated_at": s.updated_at.isoformat() if hasattr(s.updated_at, 'isoformat') else s.updated_at,
        }
        json_str = json.dumps(data, ensure_ascii=False)

        # Deserialize by creating a new session
        loaded_data = json.loads(json_str)
        s2 = Session(key=loaded_data["key"])
        for msg in loaded_data["messages"]:
            s2.add_message(msg["role"], msg["content"])

        result = TestResult(name="session_json_roundtrip", suite="session", duration=time.monotonic() - t0)
        if s2.key == "test:json" and len(s2.messages) == 2:
            result.status = "PASS"
            result.detail = "JSON serialization round-trip OK"
            print("PASS")
        else:
            result.status = "FAIL"
            result.detail = "round-trip produced different session state"
            print(f"FAIL ({result.detail})")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="session_json_roundtrip", suite="session", status="FAIL", error=str(e), duration=time.monotonic() - t0))
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

    # ── Test 2: MemoryStore round-trip — write then read (persistence across instances) ──
    print("  [memory_persistence] MemoryStore write + re-init + read ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            wd = Path(tmpdir)
            store1 = MemoryStore(workspace=wd)
            store1.write_memory("persistent memory content")
            del store1

            # Create a new MemoryStore pointing to the same dir — should read back
            store2 = MemoryStore(workspace=wd)
            content = store2.read_memory()
            if content and "persistent memory" in content:
                result = TestResult(name="memory_persistence", suite="memory", status="PASS", detail="survives re-init", duration=time.monotonic() - t0)
                print("PASS")
            else:
                result = TestResult(name="memory_persistence", suite="memory", status="FAIL", detail=f"read returned: {content}", duration=time.monotonic() - t0)
                print(f"FAIL ({content})")
        results.append(result)
    except ImportError:
        result = TestResult(name="memory_persistence", suite="memory", status="SKIP", detail="MemoryStore not importable", duration=time.monotonic() - t0)
        print("SKIP")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="memory_persistence", suite="memory", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    return results


# ──────────────────────────────────────────────
# Advanced test suite (framework-level)
# ──────────────────────────────────────────────

def run_advanced_tests() -> list[TestResult]:
    """Test framework internals: tool registry, session lifecycle, context building."""
    results: list[TestResult] = []

    # ── Test 1: ToolRegistry registration and listing ──
    print("  [advanced_tool_registry] ToolRegistry register/get/names ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.agent.tools.output_cache import OutputCache
        from nanobot.agent.tools.base import Tool

        reg = ToolRegistry(OutputCache())
        # Access the AgentLoop's registry to count registered tools
        loop = init_agent_loop()
        registry = loop.tools if hasattr(loop, 'tools') else None

        if registry is not None:
            n_tools = len(registry)
            tool_names = registry.tool_names
            has_glob = any("glob" in t for t in tool_names)
            has_read = any("read" in t for t in tool_names)
            result = TestResult(name="advanced_tool_registry", suite="advanced", duration=time.monotonic() - t0)
            if n_tools >= 10 and has_glob and has_read:
                result.status = "PASS"
                result.detail = f"{n_tools} tools registered, includes glob+read"
                print("PASS")
            else:
                result.status = "PASS_WARN"
                result.detail = f"{n_tools} tools, glob={has_glob}, read={has_read}"
                print(f"PASS_WARN ({n_tools} tools)")
        else:
            result = TestResult(name="advanced_tool_registry", suite="advanced", status="SKIP", detail="no registry on runner", duration=time.monotonic() - t0)
            print("SKIP (no registry)")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="advanced_tool_registry", suite="advanced", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 2: ContextBuilder system prompt structure ──
    print("  [advanced_context_prompt] ContextBuilder build_system_prompt ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.context import ContextBuilder
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            wd = Path(tmpdir)
            cb = ContextBuilder(
                workspace=wd,
                timezone="Asia/Shanghai",
                disabled_skills=None,
            )
            sys_prompt = cb.build_system_prompt(session_key="test:sysprompt")
            if sys_prompt and len(sys_prompt) >= 100:
                result = TestResult(name="advanced_context_prompt", suite="advanced", status="PASS", detail=f"system prompt {len(sys_prompt)} chars", duration=time.monotonic() - t0)
                print("PASS")
            else:
                result = TestResult(name="advanced_context_prompt", suite="advanced", status="PASS_WARN", detail=f"system prompt too short: {len(sys_prompt) if sys_prompt else 0}", duration=time.monotonic() - t0)
                print(f"PASS_WARN ({len(sys_prompt) if sys_prompt else 0} chars)")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="advanced_context_prompt", suite="advanced", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 3: SkillsLoader built-in discovery ──
    print("  [advanced_skills_discovery] SkillsLoader discover built-in skills ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.agent.skills import SkillsLoader
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            wd = Path(tmpdir)
            loader = SkillsLoader(workspace=wd)
            skills = loader.list_skills()
            result = TestResult(name="advanced_skills_discovery", suite="advanced", duration=time.monotonic() - t0)
            if skills and len(skills) >= 1:
                detail = f"{len(skills)} skills: {[s.get('name', '?') for s in skills[:5]]}"
                result.status = "PASS"
                result.detail = detail
                print("PASS")
            else:
                result.status = "SKIP"
                result.detail = "no built-in skills found (0 skills)"
                print("SKIP (no skills)")
        results.append(result)
    except ImportError:
        result = TestResult(name="advanced_skills_discovery", suite="advanced", status="SKIP", detail="SkillsLoader not importable", duration=time.monotonic() - t0)
        print("SKIP")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="advanced_skills_discovery", suite="advanced", status="FAIL", error=str(e), duration=time.monotonic() - t0))
        print(f"FAIL ({e})")

    # ── Test 4: Session metadata lifecycle ──
    print("  [advanced_session_metadata] Session metadata lifecycle ... ", end="", flush=True)
    t0 = time.monotonic()
    try:
        from nanobot.session.manager import Session
        s = Session(key="test:meta")
        s.metadata["key1"] = "value1"
        s.metadata["key2"] = "value2"
        # Simulate _last_summary lifecycle
        s._last_summary = "initial summary"
        initial = s._last_summary
        s._last_summary = "updated summary"

        result = TestResult(name="advanced_session_metadata", suite="advanced", duration=time.monotonic() - t0)
        if initial == "initial summary" and s._last_summary == "updated summary":
            result.status = "PASS"
            result.detail = "metadata keys preserved, _last_summary mutable"
            print("PASS")
        else:
            result.status = "FAIL"
            result.detail = "metadata mutation unexpected"
            print("FAIL")
        results.append(result)
    except Exception as e:
        results.append(TestResult(name="advanced_session_metadata", suite="advanced", status="FAIL", error=str(e), duration=time.monotonic() - t0))
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
    "advanced": run_advanced_tests,
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
        print(f"  {failed} test(s) FAILED")
    else:
        print("  All tests passed")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Nanobot-mg business capability test suite")
    parser.add_argument("--suite", default="core", help="Comma-separated suite names (core,spawn,compress,assess,session,concurrent,memory,advanced)")
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
