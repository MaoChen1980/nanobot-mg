"""Apply file edits by providing structured edit instructions."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.agent.tools import file_state
from nanobot.agent.tools.base import Tool, Validator, tool_parameters
from nanobot.agent.tools.danger import danger_warning
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p, build_parameters_schema


@dataclass(slots=True)
class _EditResult:
    path: str
    action: str
    added: int = 0
    deleted: int = 0
    then_grep: str | None = None
    error: str | None = None
    stale_warning: str | None = None


class _PatchError(ValueError):
    pass


def _validate_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise _PatchError("patch path cannot be empty")
    if "\0" in normalized:
        raise _PatchError(f"patch path contains a null byte: {path!r}")
    if any(part == ".." for part in re.split(r"[\\/]+", normalized)):
        raise _PatchError(f"patch path must not contain '..': {path}")
    return normalized


def _lines_to_text(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _text_line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _line_diff_stats(before: str, after: str) -> tuple[int, int]:
    before_lines = before.replace("\r\n", "\n").splitlines()
    after_lines = after.replace("\r\n", "\n").splitlines()
    added = 0
    deleted = 0
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted


def _format_summary(summary: _EditResult) -> str:
    stats = ""
    if summary.added or summary.deleted:
        stats = f" (+{summary.added}/-{summary.deleted})"
    return f"- {summary.action} {summary.path}{stats}"


_EDIT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "path": p("string", "Absolute path to the file to edit. Must not contain '..'."),
        "action": p("string", "Operation: 'replace' to replace exact old_text with new_text; 'add' to append to file or create new file.", enum=["replace", "add"]),
        "old_text": p("string", "Required for replace. Exact text to search for — must be unique in the file. Include enough surrounding context for disambiguation.", nullable=True),
        "new_text": p("string", "Required for replace and add. For replace: text to replace old_text with. For add: content to append (or file content if creating new file).", nullable=True),
        "then_grep": p("string",
            "Optional. After writing, search the file for this exact substring and return "
            "matching lines + line numbers to verify the edit landed correctly. "
            "Useful for multi-file edits where you want to confirm each change.",
            nullable=True,
        ),
    },
    "required": ["path", "action"],
}

_APPLY_PATCH_SCHEMA = build_parameters_schema(
    edits={
        "type": "array",
        "items": _EDIT_ITEM_SCHEMA,
        "description": "List of edits to apply. Supports multi-file changes in one call. Max 20 items.",
        "minItems": 1,
        "maxItems": 20,
    },
    dry_run=p("boolean", "If true, validate edits and show summary without writing any files. Use this to preview changes first.", default=False),
    danger_override=p("boolean",
        "When true, bypasses danger detection for stale files or destructive edits. "
        "Use only after verifying all edits are safe. "
        "Default: false. Detection re-enables automatically for the next call.",
        default=False,
    ),
    required=["edits"],
)


class _EditsPreValidate(Validator):
    """Pre-validator: 'replace' paths exist and are files."""

    async def check(self, tool: Tool, params: dict[str, Any], result: Any = None) -> str | None:
        edits = params.get("edits")
        if not edits or not isinstance(edits, list):
            return None
        for i, edit in enumerate(edits):
            if not isinstance(edit, dict):
                continue
            path = edit.get("path")
            if not isinstance(path, str):
                continue
            action = edit.get("action")
            if action != "replace" or not isinstance(path, str):
                continue
            try:
                resolved = self._resolve(tool, path)
            except Exception as e:
                return f"edits[{i}].path cannot be resolved: {e}"
            if not resolved.exists():
                return f"edits[{i}].path does not exist: {resolved.as_posix()}"
            if not resolved.is_file():
                return f"edits[{i}].path is a directory, expected a file: {resolved.as_posix()}"
        return None


@tool_parameters(_APPLY_PATCH_SCHEMA)
class EditFilesTool(_FsTool):
    """Apply file edits by providing structured edit instructions."""
    name = "edit_files_tool"

    _pre_validators = [_EditsPreValidate()]

    description = (
        "**Purpose**: Edit multiple files in a single call (batch edits). Supports replace and append operations.\n\n"
        "**When to use**:\n"
        "- Need to modify multiple files at once (preferred over multiple edit_file calls)\n"
        "- Need to replace exact text in a file (replace action)\n"
        "- Need to append content to a file or create a new file (add action)\n"
        "- Need to preview changes before applying (dry_run=true)\n\n"
        "**Notes**:\n"
        "- Paths use absolute paths\n"
        "- old_text for replace must be unique in the file at the time of edit\n"
        "- Edits to the same file are processed sequentially (each later edit sees the pending result)\n"
        "- SHA256 verified before write: file must not have changed since read\n"
        "- Auto-validates Python syntax after editing\n"
        "- Auto-rolls back all written files on failure\n"
        "- Max 20 edits per call"
    )

    async def execute(
        self,
        edits: list[dict] | None = None,
        dry_run: bool = False,
        danger_override: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            if not edits:
                return "Error: must provide edits"

            writes: dict[Path, str] = {}
            results: list[_EditResult] = []
            read_hashes: dict[Path, str] = {}

            for i, edit in enumerate(edits):
                result = _EditResult(path="", action="")
                try:
                    if not isinstance(edit, dict):
                        raise _PatchError(f"edit[{i}] must be an object")
                    raw_path = edit.get("path")
                    if not isinstance(raw_path, str):
                        raise _PatchError(f"edit[{i}] path required")
                    path = _validate_path(raw_path)
                    action = edit.get("action")
                    if not isinstance(action, str):
                        raise _PatchError(f"no action for {path}")

                    result.path = path
                    result.action = action
                    result.then_grep = edit.get("then_grep")

                    if action == "add":
                        new_text = edit.get("new_text")
                        if new_text is None:
                            raise _PatchError(f"new_text required for add: {path}")

                        source = self._resolve(path)
                        pending = writes.get(source)
                        if pending is not None:
                            content = pending
                            exists = True
                        elif source.exists():
                            raw = source.read_bytes()
                            current_hash = hashlib.sha256(raw).hexdigest()
                            prev_hash = file_state.get_content_hash(source)
                            if prev_hash and current_hash != prev_hash:
                                result.stale_warning = "Warning: file has been modified since last read. Re-read to verify content before editing."
                            read_hashes[source] = current_hash
                            try:
                                content = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                raise _PatchError(f"file is not UTF-8 text: {path}")
                            exists = True
                        else:
                            content = ""
                            exists = False

                        if exists:
                            uses_crlf = "\r\n" in content
                            new_norm = content.replace("\r\n", "\n") + new_text.replace("\r\n", "\n")
                            if new_norm and not new_norm.endswith("\n"):
                                new_norm += "\n"
                            if uses_crlf:
                                new_norm = new_norm.replace("\n", "\r\n")
                            writes[source] = new_norm
                            added, deleted = _line_diff_stats(content, new_norm)
                            result.action = "update"
                        else:
                            new_norm = new_text.replace("\r\n", "\n")
                            if new_norm and not new_norm.endswith("\n"):
                                new_norm += "\n"
                            writes[source] = new_norm
                            added = _text_line_count(new_norm)
                            deleted = 0
                            result.action = "add"

                        result.added = added
                        result.deleted = deleted

                    elif action == "replace":
                        old_text = edit.get("old_text") or ""
                        if not old_text:
                            raise _PatchError(f"old_text required for replace: {path}")
                        new_text = edit.get("new_text")
                        if new_text is None:
                            raise _PatchError(f"new_text required for replace: {path}")

                        source = self._resolve(path)
                        pending = writes.get(source)
                        if pending is not None:
                            content = pending
                        elif source.exists():
                            raw = source.read_bytes()
                            current_hash = hashlib.sha256(raw).hexdigest()
                            prev_hash = file_state.get_content_hash(source)
                            if prev_hash and current_hash != prev_hash:
                                result.stale_warning = "Warning: file has been modified since last read. Re-read to verify content before editing."
                            read_hashes[source] = current_hash
                            try:
                                content = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                raise _PatchError(f"file is not UTF-8 text: {path}")
                        else:
                            raise _PatchError(f"file to update does not exist: {path}")

                        if pending is None and not source.is_file():
                            raise _PatchError(f"path to update is not a file: {path}")

                        uses_crlf = "\r\n" in content
                        norm_content = content.replace("\r\n", "\n")
                        norm_old = old_text.replace("\r\n", "\n")

                        pos = norm_content.find(norm_old)
                        if pos < 0:
                            raise _PatchError(f"old_text not found in {path}")
                        if norm_content.find(norm_old, pos + 1) >= 0:
                            raise _PatchError(f"old_text appears multiple times in {path}")

                        new_norm = (
                            norm_content[:pos]
                            + new_text.replace("\r\n", "\n")
                            + norm_content[pos + len(norm_old) :]
                        )
                        if new_norm and not new_norm.endswith("\n"):
                            new_norm += "\n"
                        if uses_crlf:
                            new_norm = new_norm.replace("\n", "\r\n")

                        writes[source] = new_norm
                        added, deleted = _line_diff_stats(content, new_norm)
                        result.added = added
                        result.deleted = deleted

                    else:
                        raise _PatchError(f"unknown action: {action}")

                except _PatchError as exc:
                    result.error = str(exc)

                results.append(result)

            if dry_run:
                return "Patch dry-run:\n" + "\n".join(
                    _format_summary(r) for r in results
                )

            # Danger detection: stale files or destructive edits
            stale_edits = [r for r in results if r.stale_warning]
            if stale_edits and not danger_override:
                stale_paths = "\n".join(
                    f"  {r.path}: {r.stale_warning}" for r in stale_edits
                )
                return danger_warning(
                    problem=f"{len(stale_edits)} file(s) have changed since last read:\n{stale_paths}",
                    risk="Editing stale files may undo changes or produce incorrect results",
                    suggestion=f"Re-read the affected files with read_file_tool to get current content, "
                               f"back up if needed (git commit or save_stage_tool), then retry the edit",
                    tool_name="edit_files_tool",
                )

            # Dangerous: large content deletion in replace operations
            destructive_edits = [
                r for r in results
                if not r.error and r.action == "replace" and r.added == 0 and r.deleted > 20
            ]
            if destructive_edits and not danger_override:
                destructive_paths = "\n".join(
                    f"  {r.path}: removing {r.deleted} lines, adding 0 lines" for r in destructive_edits
                )
                return danger_warning(
                    problem=f"{len(destructive_edits)} edit(s) remove content without adding any:\n{destructive_paths}",
                    risk="Accidental content deletion — may cause data loss",
                    suggestion="Back up affected files first (git commit or save_stage_tool), "
                               "then verify the old_text is correct before proceeding",
                    tool_name="edit_files_tool",
                )

            # Backup files that will be written
            backups: dict[Path, bytes | None] = {}
            for path in writes:
                backups[path] = path.read_bytes() if path.exists() else None

            # Write all files, with SHA verification for files read from disk
            write_errors: list[str] = []
            for path, content in writes.items():
                try:
                    original_hash = read_hashes.get(path)
                    if original_hash:
                        if not path.exists():
                            write_errors.append(f"  {path.as_posix()}: file was deleted before write")
                            continue
                        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                        if current_hash != original_hash:
                            write_errors.append(f"  {path.as_posix()}: file changed on disk since read (SHA mismatch)")
                            continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8", newline="")
                except Exception as exc:
                    write_errors.append(f"  {path.as_posix()}: {exc}")
                    # Restore this file
                    data = backups.get(path)
                    if data is None:
                        if path.exists():
                            path.unlink()
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)

            # Record successful writes
            if not write_errors:
                for path in writes:
                    file_state.record_write(path)

            # Post-write verification: then_grep + syntax check
            verify_lines: list[str] = []
            for result in results:
                if result.error:
                    continue
                fp = self._resolve(result.path)
                if not fp.exists():
                    continue
                if result.then_grep:
                    content = fp.read_text(encoding="utf-8")
                    matches = []
                    for i, line in enumerate(content.splitlines(), 1):
                        if result.then_grep in line:
                            text = line.strip()
                            if len(text) > 120:
                                text = text[:117] + "..."
                            matches.append(f"L{i}:{text}")
                    if matches:
                        verify_lines.append(f"  {result.path} [{result.then_grep!r}]: {'; '.join(matches[:5])}")
                    else:
                        verify_lines.append(f"  {result.path} [{result.then_grep!r}]: NOT FOUND")
                if result.action in ("update", "add") and result.added:
                    syntax_error = self._check_syntax(fp)
                    if syntax_error:
                        verify_lines.append(f"  {result.path}: {syntax_error}")

            # Build report
            lines = []
            ok_count = sum(1 for r in results if not r.error)
            err_count = sum(1 for r in results if r.error)
            lines.append(f"Patch applied: {ok_count} ok, {err_count} failed")
            for r in results:
                l = _format_summary(r)
                if r.stale_warning:
                    l += f"  ⚠ {r.stale_warning}"
                if r.error:
                    l += f"  ✗ {r.error}"
                lines.append(l)
            if write_errors:
                lines.append("Write errors:")
                lines.extend(write_errors)
            if verify_lines:
                lines.append("Verification:")
                lines.extend(verify_lines)

            return "\n".join(lines)

        except PermissionError as exc:
            return f"Error: {exc}"
        except _PatchError as exc:
            return f"Error applying patch: {exc}"
        except Exception as exc:
            return f"Error applying patch: {exc}"
