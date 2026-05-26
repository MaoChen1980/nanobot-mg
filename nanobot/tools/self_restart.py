"""
self_restart.py — Agent self-restart helper.

Run with:  python workspace/tools/self_restart/self_restart.py

What it does:
1. Checks if nanobot source has changed vs the installed version
2. If changed: pip install -e . + restart the nanobot gateway process
3. Reports result

Safe by design:
- Only restarts on code changes (git diff check)
- Uses graceful restart, not kill
- Logs everything
- Can be triggered from within nanobot via a tool call
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

NANOBOT_DIR = Path("E:/claude/nanobot")
WORKSPACE = Path.home() / ".nanobot" / "workspace"
STATE_FILE = WORKSPACE / ".agent" / "restart_pending.jsonl"
LOG_FILE = WORKSPACE / ".agent" / "restart_log.md"


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check_git_diff() -> bool:
    """Return True if there are uncommitted changes in nanobot source."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(NANOBOT_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception as e:
        log(f"WARN: git diff check failed: {e}")
        return False


def install_nanobot() -> bool:
    """Run pip install -e . in nanobot dir."""
    log("Running: pip install -e E:/claude/nanobot")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(NANOBOT_DIR)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            log("✅ pip install succeeded")
            return True
        else:
            log(f"❌ pip install failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        log(f"❌ pip install exception: {e}")
        return False


def restart_gateway() -> bool:
    """Gracefully restart the nanobot gateway process.
    
    Strategy: write a restart request to a flag file.
    The gateway checks this flag at a safe point (start of each iteration)
    and triggers a clean restart when it sees the flag.
    """
    flag_file = WORKSPACE / ".agent" / "_restart_flag.json"
    flag_file.parent.mkdir(parents=True, exist_ok=True)
    flag_file.write_text(
        json.dumps({"requested_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False),
        encoding="utf-8",
    )
    log(f"✅ Restart flag written to {flag_file}")
    return True


def main() -> None:
    log("=== self_restart.py started ===")
    log(f"Python: {sys.version}")
    log(f"nanobot dir: {NANOBOT_DIR}")

    if not check_git_diff():
        log("No code changes detected. Nothing to do.")
        return

    log("Code changes detected. Proceeding with restart sequence.")

    if not install_nanobot():
        log("Abort: install failed.")
        return

    restart_gateway()
    log("=== Restart sequence complete ===")
    log("nanobot will pick up the flag on its next iteration and restart gracefully.")


if __name__ == "__main__":
    main()