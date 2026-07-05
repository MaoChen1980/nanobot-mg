"""File system tools: read, write, edit, list."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable


from nanobot.agent.tools.base import Tool


# Allow test-time override (set by test via filesystem module)
_get_media_dir_override: "Callable[[], Path] | None" = None


def _resolve_path(
    path: str,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    extra_allowed_dirs: list[Path] | None = None,
) -> Path:
    """Resolve an absolute path and enforce directory restriction.

    Raises ValueError if path is not absolute — relative paths are rejected
    unconditionally; the caller must always supply an absolute path.
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        hint = " Resolve $WORKSPACE to the actual workspace path." if "$WORKSPACE" in path else ""
        raise ValueError(f"Path must be absolute, got: {path}.{hint}")
    resolved = p.resolve()
    if allowed_dir:
        from nanobot.agent.tools import filesystem as _fs_mod
        gmd = _fs_mod.get_media_dir if _get_media_dir_override is None else _get_media_dir_override
        media_path = gmd().resolve()
        all_dirs = [allowed_dir.resolve()] + [media_path] + [d.resolve() for d in (extra_allowed_dirs or [])]
        if not any(_is_under(resolved, d) for d in all_dirs):
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir.as_posix()}")
    return resolved


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory.resolve())
        return True
    except ValueError:
        return False


class _FsTool(Tool):
    """Shared base for filesystem tools — common init and path resolution."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._extra_allowed_dirs = extra_allowed_dirs

    def _resolve(self, path: str) -> Path:
        return _resolve_path(path, self._workspace, self._allowed_dir, self._extra_allowed_dirs)

    @staticmethod
    def _find_in_file(fp: Path, pattern: str, max_matches: int = 5) -> str:
        """Search *pattern* in *fp* and return a compact verification result."""
        try:
            content = fp.read_text(encoding="utf-8")
        except Exception as e:
            return f"Verification: could not read file — {e}"

        lines = content.split("\n")
        matches: list[tuple[int, str]] = []
        for i, line in enumerate(lines, 1):
            if pattern in line:
                text = line.strip()
                if len(text) > 120:
                    text = text[:117] + "..."
                matches.append((i, text))

        if not matches:
            return f"Verification FAILED: pattern {pattern!r} not found in {fp.name}"

        result = f"Verification: pattern {pattern!r} found at line {matches[0][0]}"
        if len(matches) > 1:
            result += f"–{matches[-1][0]} ({len(matches)} matches)"
        for line_no, text in matches[:max_matches]:
            result += f"\n  {line_no}:{text}"
        if len(matches) > max_matches:
            result += f"\n  … and {len(matches) - max_matches} more"
        return result

    @staticmethod
    def _check_syntax(fp: Path, timeout: float = 10.0) -> str | None:
        """Syntax-check a Python file via py_compile (no code execution)."""
        if fp.suffix.lower() != ".py":
            return None
        try:
            import py_compile
            py_compile.compile(fp, doraise=True)
        except py_compile.PyCompileError as e:
            try:
                pyright_result = subprocess.run(
                    ["pyright", "--", str(fp)],
                    capture_output=True, text=True, timeout=30,
                )
                if pyright_result.returncode != 0:
                    errors = pyright_result.stdout.strip()
                    return f"Syntax check failed:\n{errors or str(e)}"
            except subprocess.TimeoutExpired:
                pass  # fall through to basic py_compile error
            except FileNotFoundError:
                return f"Syntax check failed (pyright not installed):\n{e}"
            return f"Syntax check failed:\n{e}"
        except FileNotFoundError:
            pass
        return None

    @staticmethod
    def _verify_write(fp: Path, pattern: str | None) -> str:
        """Verify a written file is correct: pattern check + syntax check for Python."""
        # 1. Pattern check
        if pattern:
            result = _FsTool._find_in_file(fp, pattern)
            if result.startswith("Verification FAILED"):
                return result  # Pattern not found is fatal
        else:
            result = "Verification: write succeeded (no pattern to check)"

        # 2. Syntax check for Python files
        syntax_error = _FsTool._check_syntax(fp)
        if syntax_error:
            return f"SYNTAX ERROR in {fp.name}:\n{syntax_error}"

        return result


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/tty", "/dev/console",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(path: str | Path) -> bool:
    """Check if path is a blocked device that could hang or produce infinite output."""
    import re
    raw = str(path)

    # Resolve symlinks to check the actual target
    try:
        resolved = str(Path(raw).resolve())
    except (OSError, ValueError):
        resolved = raw

    if raw in _BLOCKED_DEVICE_PATHS or resolved in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/\d+/fd/[012]$", raw) or re.match(r"/proc/self/fd/[012]$", raw):
        return True
    if re.match(r"/proc/\d+/fd/[012]$", resolved) or re.match(r"/proc/self/fd/[012]$", resolved):
        return True

    # Windows reserved device names (CON, NUL, etc.) and NT namespace paths
    if sys.platform == "win32":
        name = Path(raw).name.upper()
        if name in {"CON", "NUL", "AUX", "PRN", "CONIN$", "CONOUT$"}:
            return True
        import re
        if re.match(r"(COM|LPT)[1-9]$", name):
            return True
        if raw.startswith("\\\\.\\"):
            return True

    return False


def _parse_page_range(pages: str, total: int) -> tuple[int, int]:
    """Parse a page range like '2-5' into 0-based (start, end) inclusive."""
    parts = pages.strip().split("-")
    if len(parts) == 1:
        p = int(parts[0])
        return max(0, p - 1), min(p - 1, total - 1)
    start = int(parts[0])
    end = int(parts[1])
    return max(0, start - 1), min(end - 1, total - 1)


# ---------------------------------------------------------------------------
# Edit helpers (shared between filesystem_write.py and filesystem_edit.py)
# ---------------------------------------------------------------------------

# Use unicode codepoints to ensure correct characters regardless of encoding
_QUOTE_TABLE = str.maketrans({
    0x2018: 0x27,   # LEFT SINGLE QUOTATION MARK → APOSTROPHE
    0x2019: 0x27,   # RIGHT SINGLE QUOTATION MARK → APOSTROPHE
    0x201C: 0x22,   # LEFT DOUBLE QUOTATION MARK → QUOTATION MARK
    0x201D: 0x22,   # RIGHT DOUBLE QUOTATION MARK → QUOTATION MARK
    0x27: 0x27,     # APOSTROPHE → APOSTROPHE (identity)
    0x22: 0x22,     # QUOTATION MARK → QUOTATION MARK (identity)
})


def _normalize_quotes(s: str) -> str:
    return s.translate(_QUOTE_TABLE)


def _line_tag(line: str) -> str:
    """Generate a 4-char hex tag for a line based on its content.

    Used by read_file (tagged output). Note: edit_file no longer uses this
    for verification — it uses SHA256 content hash instead.
    Deterministic — same line always produces the same tag.
    """
    import hashlib
    return hashlib.md5(line.encode()).hexdigest()[:4]