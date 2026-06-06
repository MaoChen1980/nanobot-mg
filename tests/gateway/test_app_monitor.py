"""Tests for GatewayApplication monitor (log errors, traceback detection, job registration)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.config.schema import Config
from nanobot.gateway.app import GatewayApplication, _looks_like_traceback


def _ts(hours_ago: int = 0, minute: int = 0) -> str:
    """Return ISO timestamp N hours ago (with tz) for synthetic log entries."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago, minutes=minute)
    return dt.isoformat()


def _make_mocked_app() -> GatewayApplication:
    app = GatewayApplication(Config())
    app.agent = MagicMock()
    app.agent.extractor = MagicMock()
    app.cron = MagicMock()
    app.cron.register_system_job = MagicMock()
    app.session_manager = MagicMock()
    app.session_manager.list_sessions = MagicMock(return_value=[])
    return app


# ---------------------------------------------------------------------------
# _looks_like_traceback
# ---------------------------------------------------------------------------


class TestLooksLikeTraceback:
    def test_traceback_header(self):
        assert _looks_like_traceback("Traceback (most recent call last):")

    def test_file_line(self):
        assert _looks_like_traceback('  File "C:\\code\\app.py", line 42, in func')

    def test_indented_code(self):
        assert _looks_like_traceback("    result = 1/0")

    def test_error_line(self):
        assert _looks_like_traceback("  ValueError: invalid literal")
        assert _looks_like_traceback("  Exception: boom")

    def test_normal_text_returns_false(self):
        assert not _looks_like_traceback("Hello world")

    def test_json_line_returns_false(self):
        assert not _looks_like_traceback('{"level": "INFO", "message": "ok"}')


# ---------------------------------------------------------------------------
# _monitor_log_errors
# ---------------------------------------------------------------------------


class TestMonitorLogErrors:
    _COMMIT = "abc123"

    def _make_log_entry(self, ts: str, level: str = "ERROR", commit: str | None = None) -> str:
        return json.dumps({
            "t": ts, "l": level, "f": "test.py", "m": "something broke",
            "v": commit or self._COMMIT,
        })

    @pytest.mark.asyncio
    async def test_no_log_name_returns(self, tmp_path):
        app = _make_mocked_app()
        app.config.logging.file = None
        deliver_fn = AsyncMock()
        await app._monitor_log_errors(deliver_fn)
        deliver_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_log_file_returns(self, tmp_path):
        app = _make_mocked_app()
        app.config.logging.file = "missing.jsonl"

        with patch("nanobot.config.paths.get_data_dir", return_value=tmp_path):
            deliver_fn = AsyncMock()
            await app._monitor_log_errors(deliver_fn)
        deliver_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_run_skips_alert(self, tmp_path):
        app = _make_mocked_app()
        log_path = tmp_path / "nanobot.jsonl"
        log_path.write_text(
            self._make_log_entry(_ts(hours_ago=1)) + "\n",
            encoding="utf-8",
        )
        app.config.logging.file = "nanobot.jsonl"

        with patch("nanobot.config.paths.get_data_dir", return_value=tmp_path):
            deliver_fn = AsyncMock()
            await app._monitor_log_errors(deliver_fn)

        deliver_fn.assert_not_called()
        cursor_path = tmp_path / ".log_check_cursor"
        assert cursor_path.exists()

    @pytest.mark.asyncio
    async def test_new_error_triggers_alert(self, tmp_path):
        app = _make_mocked_app()
        log_path = tmp_path / "nanobot.jsonl"
        log_path.write_text(
            self._make_log_entry(_ts(hours_ago=1)) + "\n",
            encoding="utf-8",
        )
        app.config.logging.file = "nanobot.jsonl"

        cursor_path = tmp_path / ".log_check_cursor"
        cursor_path.write_text(_ts(hours_ago=2))

        app.session_manager.list_sessions = MagicMock(return_value=[
            {"key": "proxy:feishu:bot1:u1", "updated_at": _ts(hours_ago=0)},
        ])

        with (
            patch("nanobot.config.paths.get_data_dir", return_value=tmp_path),
            patch("nanobot.utils.logging._COMMIT", self._COMMIT),
        ):
            deliver_fn = AsyncMock()
            await app._monitor_log_errors(deliver_fn)

        deliver_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_old_errors_skipped(self, tmp_path):
        app = _make_mocked_app()
        log_path = tmp_path / "nanobot.jsonl"
        log_path.write_text(
            self._make_log_entry(_ts(hours_ago=72)) + "\n",
            encoding="utf-8",
        )
        app.config.logging.file = "nanobot.jsonl"

        cursor_path = tmp_path / ".log_check_cursor"
        cursor_path.write_text(_ts(hours_ago=73))

        with (
            patch("nanobot.config.paths.get_data_dir", return_value=tmp_path),
            patch("nanobot.utils.logging._COMMIT", self._COMMIT),
        ):
            deliver_fn = AsyncMock()
            await app._monitor_log_errors(deliver_fn)

        deliver_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_caps_at_fifteen_errors(self, tmp_path):
        app = _make_mocked_app()
        lines = "\n".join(
            self._make_log_entry(_ts(hours_ago=1, minute=i)) for i in range(30)
        )
        log_path = tmp_path / "nanobot.jsonl"
        log_path.write_text(lines + "\n", encoding="utf-8")
        app.config.logging.file = "nanobot.jsonl"

        cursor_path = tmp_path / ".log_check_cursor"
        cursor_path.write_text(_ts(hours_ago=2))

        app.session_manager.list_sessions = MagicMock(return_value=[
            {"key": "proxy:feishu:bot1:u1", "updated_at": _ts(hours_ago=0)},
        ])

        with (
            patch("nanobot.config.paths.get_data_dir", return_value=tmp_path),
            patch("nanobot.utils.logging._COMMIT", self._COMMIT),
        ):
            deliver_fn = AsyncMock()
            await app._monitor_log_errors(deliver_fn)

        deliver_fn.assert_called_once()
        alert_text = deliver_fn.call_args[0][0].content
        assert "15 more" in alert_text


# ---------------------------------------------------------------------------
# _register_log_check_job
# ---------------------------------------------------------------------------


class TestRegisterLogCheckJob:
    def test_registers_log_check_job(self):
        app = _make_mocked_app()
        with patch("nanobot.gateway.app.console.print"):
            app._register_log_check_job()

        app.cron.register_system_job.assert_called_once()
        job = app.cron.register_system_job.call_args[0][0]
        assert job.id == "log_check"
        assert job.name == "log_check"
        assert job.schedule.kind == "every"


# ---------------------------------------------------------------------------
# _register_self_review_jobs
# ---------------------------------------------------------------------------


class TestRegisterSelfReviewJobs:
    def test_registers_three_jobs(self):
        app = _make_mocked_app()
        with patch("nanobot.gateway.app.console.print"):
            app._register_self_review_jobs()

        assert app.cron.register_system_job.call_count == 3
        ids = [call[0][0].id for call in app.cron.register_system_job.call_args_list]
        assert "daily-self-review" in ids
        assert "daily-tool-optimizer" in ids
        assert "daily-evolution" in ids
