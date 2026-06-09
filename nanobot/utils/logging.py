"""Logging configuration and initialization."""

import subprocess
from pathlib import Path

from loguru import logger

from nanobot.config.paths import get_data_dir
from nanobot.config.schema import LogConfig


def _get_git_commit() -> str:
    """Return short git commit hash.

    Priority:
      1. nanobot._commit (build-time — survives pip install)
      2. git rev-parse (runtime — works in source checkout)
      3. "unknown"
    """
    try:
        from nanobot._commit import __commit__  # type: ignore[import-untyped]
        if __commit__:
            return __commit__
    except (ImportError, AttributeError):
        pass
    try:
        project_root = Path(__file__).resolve().parent.parent.parent
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(project_root),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        from nanobot import __version__
        if __version__:
            return __version__
    except (ImportError, AttributeError):
        pass
    return "unknown"


_COMMIT = _get_git_commit()

_FORMAT = "{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level: <8} | [" + _COMMIT + "] | {name}:{function}:{line} - {message}"
_JSON_FORMAT = '{{"t":"{time:YYYY-MM-DDTHH:mm:ss.SSSZ}","v":"' + _COMMIT + '","l":"{level}","n":"{name}","f":"{function}:{line}","m":"{message}"}}'


class LoggerConfig:
    """Logger configuration manager."""

    def __init__(self):
        self._configured = False

    def configure(self, log_config: LogConfig) -> None:
        """Configure logger based on LogConfig."""
        if self._configured:
            return

        # Remove default handler
        logger.remove()

        if not log_config.enabled:
            return

        # Add console handler if enabled
        if log_config.console:
            logger.add(
                sink=lambda msg: print(msg, end=""),
                level=log_config.level,
                format="<green>{time:YYYY-MM-DDTHH:mm:ss.SSSZ}</green> | <level>{level: <8}</level> | [<yellow>" + _COMMIT + r"</yellow>] | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
            )

        # Add JSONL log file (machine-parseable, agent-friendly)
        if log_config.file:
            log_path = get_data_dir() / log_config.file
            log_path.parent.mkdir(parents=True, exist_ok=True)

            logger.add(
                sink=log_path,
                level=log_config.level,
                format=_JSON_FORMAT,
                rotation="5 MB",
                retention="3 days",
                compression="zip",
            )

        # ERROR+ only log — always on, compact, for quick debugging
        if log_config.error_file:
            err_path = get_data_dir() / log_config.error_file
            err_path.parent.mkdir(parents=True, exist_ok=True)

            logger.add(
                sink=err_path,
                level="ERROR",
                format=_FORMAT,
                rotation="10 MB",
                retention="30 days",
                compression="zip",
            )

        self._configured = True


# Global logger config instance
logger_config = LoggerConfig()
