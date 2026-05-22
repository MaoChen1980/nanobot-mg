"""Hatchling build hook — embed git commit hash into _commit.py."""

import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

REPO_ROOT = Path(__file__).resolve().parent.parent


class WriteCommitHashBuildHook(BuildHookInterface):
    """Generate nanobot/_commit.py with the git commit hash at build time."""

    def initialize(self, version, build_data):
        commit = _get_commit()
        dst = REPO_ROOT / "nanobot" / "_commit.py"
        dst.write_text(f'"""Build-time commit hash."""\n__commit__ = "{commit}"\n')

        # Force-include so the file survives into the wheel even if
        # hatchling resolved the file list before the hook ran.
        build_data.setdefault("force_include", {})
        build_data["force_include"][str(dst)] = "nanobot/_commit.py"


def _get_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(REPO_ROOT),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"
