"""Analyze tool — read data and extract structured insights in one call."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.filesystem.filesystem_base import _FsTool
from nanobot.agent.tools.schema import p, build_parameters_schema


@tool_parameters(
    build_parameters_schema(
        data=p("string", "Text content to analyze (provide this or path)"),
        path=p("string", "Absolute path to a file to read and analyze. Provide this or data."),
        keyword=p("string", "Optional keyword filter — simple term matching, NOT semantic analysis. Example: 'database connection error' shows lines containing those words ranked by match count. (Legacy alias: question)"),
        max_keywords=p("integer", "Maximum keywords to extract (default 15)", minimum=1, maximum=50, default=15),
    ),
    required=[],
)
class AnalyzeTool(_FsTool):
    """Read data (from text or file) and return structured analysis."""

    name = "analyze_tool"
    read_only = True

    description = (
        "**Purpose**: Analyze text and return a structured summary (line stats, keywords, keyword-filtered), without reading the full text into context.\n\n"
        "**When to use**:\n"
        "- File is too large (e.g. logs) — analyze it first to get an overview, then decide which section to read\n"
        "- You want to know the topic of a file without reading the full content — analyze extracts keywords\n"
        "- You want to find specific types of content (errors, warnings) — pass a keyword\n\n"
    )

    MAX_TEXT_SIZE = 500_000

    async def execute(
        self,
        data: str | None = None,
        path: str | None = None,
        keyword: str | None = None,
        max_keywords: int = 15,
        **kwargs: Any,
    ) -> str:
        # Backwards compat: legacy alias "question"
        if keyword is None and kwargs.get("question"):
            keyword = kwargs["question"]
        text = await self._load_text(data, path)
        if text is None:
            return "Error: Provide either `data` (text) or `path` (file) to analyze."
        if isinstance(text, str) and text.startswith("Error"):
            return text

        lines = text.split("\n")
        total_lines = len(lines)
        total_chars = len(text)
        words = text.split()
        total_words = len(words)

        parts = ["# Analysis"]

        parts.append("\n## Overview")
        parts.append(f"- Lines: {total_lines:,}")
        parts.append(f"- Words: {total_words:,}")
        parts.append(f"- Characters: {total_chars:,}")

        sections = self._detect_sections(lines)
        if sections:
            parts.append(f"\n## Sections ({len(sections)})")
            for title, size, preview in sections[:20]:
                parts.append(f"- \"{title}\" ({size} lines)  {preview}")

        kw = self._extract_keywords(text, max_keywords)
        if kw:
            parts.append(f"\n## Keywords")
            parts.append(", ".join(f"`{w}`" for w, _ in kw[:max_keywords]))

        if keyword:
            parts.append(f"\n## Keyword filter: {keyword}")
            q_lines = self._find_relevant_lines(lines, keyword)
            if q_lines:
                for lineno, line in q_lines[:15]:
                    parts.append(f"  L{lineno}: {line[:120]}")
            else:
                parts.append("  (No directly matching lines found)")

        parts.append(f"\n---\n({total_lines} lines, {total_words} words)")
        return "\n".join(parts)

    async def _load_text(self, data: str | None, path: str | None) -> str | None:
        if data:
            return data[:self.MAX_TEXT_SIZE]
        if path:
            try:
                fp = self._resolve(path)
                if not fp.exists():
                    return f"Error: File not found: {path}"
                if fp.is_dir():
                    return f"Error: Path is a directory, not a file: {path}"
                if fp.stat().st_size > self.MAX_TEXT_SIZE:
                    return f"Error: File too large ({fp.stat().st_size:,} bytes)"
                raw = fp.read_bytes()
                return raw.decode("utf-8")[:self.MAX_TEXT_SIZE]
            except UnicodeDecodeError:
                return f"Error: Cannot read binary file: {path}"
            except OSError as e:
                return f"Error: {e}"
        return None

    @staticmethod
    def _detect_sections(lines: list[str]) -> list[tuple[str, int, str]]:
        sections: list[tuple[str, int, str]] = []
        start = 0
        current_title = "(top)"

        for i, line in enumerate(lines):
            title = None
            m = re.match(r"^#{1,6}\s+(.+)$", line.strip())
            if m:
                title = m.group(1).strip()
            elif re.match(r"^={3,}$", line.strip()) and i > 0 and lines[i - 1].strip():
                title = lines[i - 1].strip()

            if title:
                section_lines = i - start
                preview = lines[start][:60] if start < len(lines) else ""
                sections.append((current_title, section_lines, preview))
                current_title = title[:60]
                start = i + 1

        if start < len(lines):
            sections.append((current_title, len(lines) - start, lines[start][:60] if start < len(lines) else ""))

        return sections

    @staticmethod
    def _extract_keywords(text: str, n: int = 15) -> list[tuple[str, int]]:
        words = re.findall(r"[a-zA-Z一-鿿_]+", text.lower())
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into", "through",
            "during", "before", "after", "above", "below", "between", "and",
            "but", "or", "nor", "not", "so", "yet", "both", "either", "neither",
            "if", "then", "else", "this", "that", "these", "those", "it", "its",
            "we", "they", "them", "our", "you", "your", "he", "she", "him",
            "her", "his", "my", "me", "all", "each", "every", "some", "any",
            "no", "none", "most", "many", "much", "few", "more", "less",
            "other", "another", "such", "own", "same", "different",
            "about", "than", "too", "very", "just", "also", "only", "now",
            "here", "there", "when", "where", "why", "how", "which", "what",
            "who", "whom", "def", "class", "return", "import", "from", "self",
            "true", "false", "none", "async", "await", "pass", "raise", "try",
            "except", "finally", "with", "yield", "lambda",
        }
        filtered = [w for w in words if w not in stopwords and len(w) > 1]
        return Counter(filtered).most_common(n)

    @staticmethod
    def _find_relevant_lines(lines: list[str], question: str) -> list[tuple[int, str]]:
        keywords = re.findall(r"[a-zA-Z一-鿿_]+", question.lower())
        keywords = [k for k in keywords if len(k) > 2]
        if not keywords:
            return []

        scored: list[tuple[int, int, str]] = []
        for i, line in enumerate(lines):
            lower = line.lower()
            score = sum(1 for k in keywords if k in lower)
            if score > 0:
                scored.append((i, score, line.strip()))

        scored.sort(key=lambda x: -x[1])
        return [(i, s) for i, _, s in scored[:20]]
