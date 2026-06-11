"""
nanobot - A lightweight AI agent framework
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


def _read_pyproject_version() -> Optional[str]:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists() or tomllib is None:
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    # Prefer git tag when running from a source checkout
    try:
        import subprocess
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--always"],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parent,
        ).decode().strip()
        if tag:
            return tag
    except Exception:
        logger.debug("Failed to resolve git version")
    try:
        return _pkg_version("nanobot-ai")
    except PackageNotFoundError:
        return _read_pyproject_version() or "0.1.5.post2"


__version__ = _resolve_version()
__logo__ = "🐈"

from nanobot.nanobot import Nanobot, RunResult

__all__ = ["Nanobot", "RunResult"]
