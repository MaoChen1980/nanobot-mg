# Cross-Platform Compatibility Audit

**Date:** 2026-05-17
**Scope:** All source files under `nanobot/`, configs, scripts, CI/CD

---

## Critical — Fix Before Shipping on macOS/Linux

### 1. `diagnose_tool.py` hardcodes `grep` command

**File:** `nanobot/agent/tools/diagnose_tool.py:128-131,137-138`
**Bug:** Uses Unix `grep -rn --include=...` and `grep -n`. `grep` is not available on stock Windows. **Will silently return zero results.**
**Fix:** Replace with pure-Python search using `Path.rglob()` and `fnmatch`, or check `shutil.which("grep")` with Python fallback.

### 2. Sandbox module is Linux-only

**File:** `nanobot/agent/tools/sandbox.py:30-31`
**Bug:** Hardcodes Linux paths (`/bin`, `/lib`, `/etc/ld.so.cache`) for `bwrap`. Will crash if sandbox is enabled on macOS/Windows.
**Fix:** Add platform check with clear error: "Sandbox requires Linux with bubblewrap (bwrap)".

### 3. `os.execv()` restart behaves differently on Windows

**File:** `nanobot/command/builtin.py:41`
**Bug:** `os.execv()` on Windows doesn't replace the process — it starts a new one while the old one continues. Can leave orphan processes.
**Fix:** On Windows, use `subprocess.Popen([sys.executable, ...])` + `sys.exit(0)` instead.

---

## High Priority

### 4. 45 Python/source files committed with CRLF line endings

**Scope:** 45 `.py`/`.md` files in the index use CRLF. On Linux/macOS, these will cause issues with shebangs, diffs, and some tooling.
**Fix:** Configure `.gitattributes` with `* text=auto` and normalize existing CRLF files.

### 5. No `* text=auto` in `.gitattributes`

**File:** `.gitattributes` (only has `*.sh text eol=lf`)
**Fix:** Add:
```
* text=auto
*.py text
*.md text
*.yml text
*.json text
*.toml text
*.sh text eol=lf
*.png binary
*.jpg binary
```

### 6. `/dev/` device path guard misses Windows special devices

**File:** `nanobot/agent/tools/filesystem/filesystem_base.py:102-131`
**Bug:** Blocks `/dev/zero`, `/dev/random` etc. on Linux, but misses Windows equivalents (`CON`, `NUL`, `AUX`, `\\.\PhysicalDrive0`). Reading `CON` would hang indefinitely.
**Fix:** Add Windows device path blocking alongside existing Linux paths.

### 7. `MEDIA_DIR` default falls back to `/tmp`

**File:** `nanobot/agent/tools/shell_validators.py:89`
**Bug:** `os.environ.get("MEDIA_DIR", "/tmp")` — on Windows, no `/tmp` exists.
**Fix:** Use `tempfile.gettempdir()` instead.

---

## Medium Priority

### 8. Missing macOS runner in CI

**File:** `.github/workflows/ci.yml`
**Issue:** CI matrix includes `ubuntu-latest` and `windows-latest` but no `macos-latest`. All macOS-specific paths (TCP_KEEPALIVE, stat, date) are untested.
**Fix:** Add `macos-latest` to CI matrix.

### 9. Terminal handling degraded on Windows

**File:** `nanobot/cli/commands.py:77-128`
**Issue:** Uses `termios` + `fcntl` in try/except — catches ImportError on Windows but silent degradation. Terminal echo/buffering not properly restored.
**Fix:** On Windows, use `kernel32.SetConsoleMode()` for proper terminal restore.

### 10. `docs/chat-commands.md` has mixed CRLF+LF line endings

**File:** `docs/chat-commands.md`
**Issue:** 29 CRLF + 1 LF line. Causes inconsistent diffs and tooling issues.
**Fix:** Normalize to single line-ending style.

### 11. No `.editorconfig`

**Issue:** Missing `.editorconfig` means no editor-level encoding/tab enforcement. Inconsistent across contributors.
**Fix:** Add `.editorconfig` with `root = true`, charset utf-8, indent style, end_of_line = lf.

---

## Low Priority

### 12. `~/.nanobot` not XDG-compliant on Linux

**Files:** Multiple — config/paths.py, gateway/app.py, agent/db.py, proxy/*.py
**Issue:** All data/config stored under `~/.nanobot`. On Linux, should follow XDG (`~/.config/nanobot`, `~/.local/share/nanobot`, `~/.cache/nanobot`).
**Note:** Keep `~/.nanobot` as backward-compatible fallback.

### 13. Python files lack explicit encoding declarations

**Issue:** No `# -*- coding: utf-8 -*-` in any `.py` file. Python 3 defaults to UTF-8, but explicit declarations are best practice.
**Fix:** Add to all Python files (optional, low impact).

### 14. Shell error handling on exit

**File:** `nanobot/cli/commands.py:639-647`
**Issue:** `signal.signal(signal.SIGTERM, ...)` has limited support on Windows. SIGHUP/SIGPIPE properly guarded with `hasattr`.

---

## Summary

| Severity | Count | Action Needed |
|----------|-------|---------------|
| Critical | 3 | Fix before macOS/Linux deployment |
| High | 4 | Address soon |
| Medium | 4 | Address when convenient |
| Low | 3 | Nice to have |

**What's good:** Shell tool (`shell.py`) has excellent `_IS_WINDOWS` branching. Proxy manager properly handles `tasklist`/`taskkill` on Windows vs `ps`/`os.kill` on Unix. TCP keepalive handles Win/Linux/macOS correctly. No `shell=True` in any subprocess call. Cross-platform test suite exists in `test_exec_platform.py`.

**Biggest single fix:** The `.gitattributes` + CRLF normalization will touch the most files but is a one-time mechanical fix.
