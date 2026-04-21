"""Logging configuration and initialization."""

from pathlib import Path
from typing import Optional

from loguru import logger

from nanobot.config.paths import get_data_dir
from nanobot.config.schema import LogConfig


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
                format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS %z}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            )

        # Add file handler if file path is provided
        if log_config.file:
            log_path = get_data_dir() / log_config.file
            log_path.parent.mkdir(parents=True, exist_ok=True)

            logger.add(
                sink=log_path,
                level=log_config.level,
                format="{time:YYYY-MM-DD HH:mm:ss.SSS %z} | {level: <8} | {name}:{function}:{line} - {message}",
                rotation="50MB",
                compression="zip",
            )

        self._configured = True


# Global logger config instance
logger_config = LoggerConfig()
