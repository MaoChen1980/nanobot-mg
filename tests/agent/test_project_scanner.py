"""Tests for project_scanner — exception paths in file scanning."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from nanobot.agent.project_scanner import (
    _estimate_loc,
    _read_config_snippet,
    scan_project,
)


class TestEstimateLoc:
    """``_estimate_loc`` — line count estimation."""

    def test_skips_unreadable_file(self, tmp_path):
        """When a file raises on read_bytes, it's skipped and count continues."""
        good = tmp_path / "good.py"
        good.write_text("line1\nline2\n")
        bad = tmp_path / "bad.py"
        bad.write_text("should not be read")

        original_read_bytes = Path.read_bytes

        def side_effect(self_inst, *args, **kwargs):
            if self_inst == bad:
                raise PermissionError("no read")
            return original_read_bytes(self_inst, *args, **kwargs)

        with patch.object(Path, "read_bytes", side_effect):
            total = _estimate_loc(tmp_path, {"py"})
            assert total >= 2

    def test_returns_zero_for_empty_dir(self, tmp_path):
        assert _estimate_loc(tmp_path, {"py"}) == 0


class TestReadConfigSnippet:
    """``_read_config_snippet`` — safe config file reading."""

    def test_returns_none_for_unreadable_file(self, tmp_path):
        """When a file raises on read_text, returns None."""
        path = tmp_path / "config.json"
        path.write_text("{}")

        original_read_text = Path.read_text

        def side_effect(self_inst, *args, **kwargs):
            if self_inst == path:
                raise PermissionError("no read")
            return original_read_text(self_inst, *args, **kwargs)

        with patch.object(Path, "read_text", side_effect):
            with patch.object(Path, "stat") as mock_stat:
                mock_stat.return_value.st_size = 100
                result = _read_config_snippet(path)

        assert result is None

    def test_returns_none_when_stat_fails(self, tmp_path):
        """When stat() itself fails, returns None."""
        path = tmp_path / "missing.json"
        result = _read_config_snippet(path)
        assert result is None

    def test_large_file_returns_placeholder(self, tmp_path):
        """File larger than 50KB returns [file too large]."""
        path = tmp_path / "big.json"
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = 100_000
            result = _read_config_snippet(path)
        assert result == "[file too large]"

    def test_empty_file_returns_none(self, tmp_path):
        """Empty or whitespace-only file returns None."""
        path = tmp_path / "empty.json"
        path.write_text("   \n  ")
        result = _read_config_snippet(path)
        assert result is None

    def test_normal_file_returns_content(self, tmp_path):
        """Normal file returns its text content."""
        path = tmp_path / "normal.json"
        path.write_text('{"key": "value"}')
        result = _read_config_snippet(path)
        assert result == '{"key": "value"}'


class TestScanProjectPyproject:
    """``scan_project`` — pyproject.toml dependency parsing."""

    def test_corrupt_pyproject_does_not_crash(self, tmp_path):
        """Corrupt pyproject.toml -> exception caught, scan continues."""
        root = tmp_path / "project"
        root.mkdir()
        pyproject = root / "pyproject.toml"
        pyproject.write_bytes(b"[project]\ndependencies = \xff\xfe\n")

        info = scan_project(root, use_real_paths=False)
        assert info.name == "project"

    def test_directory_without_pyproject(self, tmp_path):
        """No pyproject.toml -> scan completes cleanly."""
        root = tmp_path / "project"
        root.mkdir()
        info = scan_project(root, use_real_paths=False)
        assert info.name == "project"
        assert info.deps_total == 0

    def test_valid_pyproject_counts_deps(self, tmp_path):
        """Valid pyproject.toml with poetry-style dependencies -> deps_total populated."""
        root = tmp_path / "project"
        root.mkdir()
        pyproject = root / "pyproject.toml"
        pyproject.write_text(
            "[tool.poetry.dependencies]\npython = \"^3.9\"\nrequests = \">=2.0\"\nclick = \"*\"\n",
            encoding="utf-8",
        )
        info = scan_project(root, use_real_paths=False)
        assert info.deps_total == 3
