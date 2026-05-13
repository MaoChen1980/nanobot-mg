"""Diagnose tool — grep codebase + git history to root-cause errors."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool


@tool_parameters(
    tool_parameters_schema(
        error=p("string", "Error message, keyword, or code to investigate"),
        path=p("string", "Optional file or directory to narrow the search scope"),
        max_results=p("integer", "Max grep results to show (default 20)", minimum=1, maximum=50),
        days=p("integer", "Days of git history to check (default 7)", minimum=1, maximum=90),
    ),
    required=["error"],
)
class DiagnoseTool(_FsTool):
    """Diagnose errors by searching code and checking recent changes."""

    name = "diagnose"
    read_only = True

    description = (
        "**用途**: 结合代码搜索和 git 历史，一站式调查错误根因。\n\n"
        "**限制**:\n"
        "- 默认搜索 7 天内的 git 历史\n"
        "- max_results 最多 50，days 最多 90\n"
        "- git 查询超时 15 秒\n\n"
        "**错误应对**:\n"
        "- 无匹配 → 扩大 days 或换 error 关键词\n"
        "- 不在 git 仓库 → git 历史部分不可用\n\n"
        "**边界条件**:\n"
        "- 只需要代码搜索 → 用 grep\n"
        "- 只需要 git 历史 → 用 git_inspect\n"
        "- 配置/环境问题 → diagnose 不适合，直接排查配置\n\n"
        "**极简案例**: diagnose(error='TypeError: object str', days=3)\n"
        "→ 返回相关代码位置和最近触及的 commits"
    )

    async def execute(
        self,
        error: str = "",
        path: str | None = None,
        max_results: int = 20,
        days: int = 7,
        **kwargs: Any,
    ) -> str:
        try:
            workspace = self._resolve(".")
            git_dir = self._find_git_root(workspace)
        except PermissionError:
            git_dir = None

        terms = self._extract_terms(error)
        parts = [f"# Diagnosis: {error[:80]}", ""]

        hits = self._search_code(terms, path, max_results)
        if hits:
            parts.append(f"## Code matches ({len(hits)} lines)")
            parts.append("")
            parts.extend(hits[:max_results])
        else:
            parts.append("## Code matches")
            parts.append("(No direct code matches found)")
        parts.append("")

        if git_dir:
            since = f"{days} days ago"
            log = self._git_log(git_dir, terms, since, 10)
            if log:
                parts.append(f"## Recent changes (last {days} days, {len(log)} commits)")
                parts.append("")
                parts.extend(log)
            else:
                parts.append(f"## Recent changes")
                parts.append(f"(No commits touching related files in the last {days} days)")
        else:
            parts.append("## Git history")
            parts.append("(Not a git repository)")

        parts.append("")
        summary_parts: list[str] = []
        if hits:
            summary_parts.append(f"found {len(hits)} code matches")
        if git_dir and log:
            summary_parts.append(f"{len(log)} recent commits")
        summary = ", ".join(summary_parts) if summary_parts else "no relevant results"
        parts.append(f"---\n({summary})")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_code(self, terms: list[str], path: str | None, max_results: int) -> list[str]:
        seen: set[str] = set()
        hits: list[str] = []
        search_root = self._resolve(path or ".")

        for term in terms[:3]:
            try:
                cmd = self._grep_cmd(term, str(search_root))
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
                    for f in files[:max_results]:
                        key = f.lower()
                        if key not in seen:
                            seen.add(key)
                            if len(hits) < max_results:
                                first_match = self._first_match_line(f, term)
                                rel = Path(f).relative_to(search_root).as_posix() if search_root in Path(f).parents else f
                                hits.append(f"  {rel}{first_match}")
            except (subprocess.TimeoutExpired, OSError):
                continue
        return hits

    @staticmethod
    def _grep_cmd(term: str, search_root: str) -> list[str]:
        return [
            "grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
            "--include=*.go", "--include=*.rs", "--include=*.java", "--include=*.kt",
            "-l", term, search_root,
        ]

    @staticmethod
    def _first_match_line(filepath: str, term: str) -> str:
        try:
            ctx = subprocess.run(
                ["grep", "-n", term, filepath],
                capture_output=True, text=True, timeout=5,
            )
            if ctx.stdout.strip():
                first = ctx.stdout.strip().split("\n")[0]
                parts = first.split(":", 2)
                if len(parts) >= 3:
                    return f"  L{parts[1]}: {parts[2][:120]}"
                return ""
        except Exception:
            return ""
        return ""

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------

    @staticmethod
    def _find_git_root(start: Path) -> Path | None:
        current = start.resolve()
        while current != current.parent:
            if (current / ".git").exists():
                return current
            current = current.parent
        return None

    @staticmethod
    def _git_log(git_dir: Path, terms: list[str], since: str, max_count: int) -> list[str]:
        log_lines: list[str] = []
        seen_shas: set[str] = set()

        for term in terms[:3]:
            try:
                cmd = [
                    "git", "-C", str(git_dir), "log",
                    f"--since={since}", f"--max-count={max_count}",
                    "--format=%h | %ai | %s",
                    f"--grep={term}", "-i", "--all",
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if result.returncode == 0 and result.stdout.strip():
                    for line in result.stdout.strip().split("\n"):
                        sha = line.split(" | ")[0] if " | " in line else line[:8]
                        if sha not in seen_shas:
                            seen_shas.add(sha)
                            log_lines.append(f"  {line}")
            except (subprocess.TimeoutExpired, OSError):
                continue
            if log_lines:
                break

        return log_lines[:max_count]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_terms(error: str) -> list[str]:
        text = error.strip().strip('"').strip("'").strip("`")
        terms: list[str] = []

        clean = text.split("\n")[0][:100]
        if clean:
            terms.append(clean)

        import re
        quoted = re.findall(r""""([^"]+)"|'([^']+)'|`([^`]+)`""", text)
        for match in quoted:
            for group in match:
                if group and len(group) > 2:
                    terms.append(group)

        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", text)
        for w in words:
            if len(w) > 3 and w.lower() not in {"error", "traceback", "exception", "file", "line", "none", "true", "false"}:
                terms.append(w)

        return terms
