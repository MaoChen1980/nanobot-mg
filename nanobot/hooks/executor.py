"""
Executor Pipeline — Execute action suggestions from Analyzers.

Input: Action JSON from self_insight / tool_optimizer
Output: Execution result (success/failure)

Safety:
- Path whitelist (workspace only)
- Operation blacklist (rm -rf, del /f, shutdown)
- Dry-run mode by default
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

# Whitelist: only allow edits within workspace
WORKSPACE_ROOT = Path.home() / ".nanobot" / "workspace"

# Blacklist: destructive operations
BLOCKED_OPS = frozenset({
    "rm -rf",
    "del /f",
    "del /s /q",
    "shutdown",
    "reboot",
    "format",
})

# Allowed action types
ALLOWED_ACTIONS = frozenset({"edit_file", "write_file", "delete_file"})


class Executor:
    """Execute analyzer suggestions with safety checks."""

    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run
        self.executed_count = 0
        self.skipped_count = 0

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute a single action."""
        action_type = action.get("action")

        # Validate action type
        if action_type not in ALLOWED_ACTIONS:
            return {"status": "skipped", "reason": f"action type '{action_type}' not allowed"}

        # Validate path
        path = action.get("path")
        if not path:
            return {"status": "skipped", "reason": "missing path"}

        path_obj = Path(path).resolve()
        if not str(path_obj).startswith(str(WORKSPACE_ROOT.resolve())):
            return {"status": "skipped", "reason": f"path outside workspace: {path}"}

        # Check for blocked ops in the action
        action_str = json.dumps(action)
        if any(blocked in action_str.lower() for blocked in BLOCKED_OPS):
            return {"status": "skipped", "reason": "blocked operation detected"}

        # Dry run - just validate
        if self.dry_run:
            logger.info(f"[DRY RUN] Would execute: {action_type} {path}")
            self.skipped_count += 1
            return {"status": "dry_run", "action": action}

        # Actually execute
        try:
            if action_type == "edit_file":
                result = self._execute_edit(action)
            elif action_type == "write_file":
                result = self._execute_write(action)
            elif action_type == "delete_file":
                result = self._execute_delete(action)
            else:
                result = {"status": "error", "reason": f"unimplemented: {action_type}"}

            self.executed_count += 1
            return result
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            return {"status": "error", "reason": str(e)}

    def _execute_edit(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute edit_file action."""
        path = action["path"]
        old_text = action.get("old_text", "")
        new_text = action.get("new_text", "")

        # Read current content
        path_obj = Path(path)
        if path_obj.exists():
            current = path_obj.read_text(encoding="utf-8")
        else:
            current = ""

        # Apply replacement
        if old_text not in current:
            return {"status": "error", "reason": "old_text not found in file"}

        new_content = current.replace(old_text, new_text, 1)
        path_obj.write_text(new_content, encoding="utf-8")

        return {"status": "success", "action": "edit_file", "path": path}

    def _execute_write(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute write_file action."""
        path = action["path"]
        content = action.get("content", "")

        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_text(content, encoding="utf-8")

        return {"status": "success", "action": "write_file", "path": path}

    def _execute_delete(self, action: dict[str, Any]) -> dict[str, Any]:
        """Execute delete_file action."""
        path = action["path"]
        path_obj = Path(path)

        if not path_obj.exists():
            return {"status": "error", "reason": "file not found"}

        path_obj.unlink()
        return {"status": "success", "action": "delete_file", "path": path}


def run_from_suggestion(suggestion_json: str, dry_run: bool = True) -> dict[str, Any]:
    """Run executor from JSON string suggestion."""
    try:
        suggestion = json.loads(suggestion_json)
    except json.JSONDecodeError:
        return {"status": "error", "reason": "invalid JSON"}

    executor = Executor(dry_run=dry_run)
    
    # Handle single action or list
    if isinstance(suggestion, list):
        results = [executor.execute(a) for a in suggestion]
        return {"results": results, "executed": executor.executed_count, "skipped": executor.skipped_count}
    else:
        return executor.execute(suggestion)


if __name__ == "__main__":
    import sys
    json_arg = sys.argv[1] if len(sys.argv) > 1 else "{}"
    result = run_from_suggestion(json_arg, dry_run=True)
    print(json.dumps(result, indent=2))