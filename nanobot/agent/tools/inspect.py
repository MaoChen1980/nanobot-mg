"""inspect_text tool — preview document structure without LLM."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nanobot.agent.tools._semantic_base import (
    _find_representative,
    extract_keywords,
    get_model,
    segment_unstructured,
)
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import p, tool_parameters_schema

_MAX_TEXT_BYTES = 5 * 1024 * 1024


@tool_parameters(
    tool_parameters_schema(
        text=p("string", "Text content to inspect (max 5 MB). Provide this or path."),
        path=p("string", "File path to inspect. Provide this or text."),
        max_sections=p("integer", "Maximum number of sections to return", minimum=1, maximum=20),
        mode=p("string", "Detection mode: auto (structure→semantic fallback), semantic (embedding segments), structure (headings/separators only)", enum=["auto", "semantic", "structure"]),
    ),
    required=[],
)
class InspectTextTool(Tool):
    """Preview a document's structure before reading it in full."""

    name = "inspect_text"
    read_only = True

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
    ) -> None:
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    description = (
        "**用途**: 预览文档结构（类目录），text 和 path 二选一传入。\n\n"
        "**限制**:\n"
        "- 输入最大 5 MB\n"
        "- 段落检测是启发式的，可能过度拆分或合并\n"
        "- 关键词基于词频，不是语义\n\n"
        "**错误应对**:\n"
        "- 返回空/单段落 → 语义模式需要 sentence-transformers 依赖\n"
        "- 依赖未安装 → 自动降级到换行拆分（精度降低）\n\n"
        "**边界条件**:\n"
        "- 已知文档内容 → 直接读，不预览\n"
        "- 需要按语义搜索 → 用 search_text\n"
        "- 需要关键词匹配 → 用 grep\n\n"
        "**极简案例**: inspect_text(path='long_doc.md', mode='structure')\n"
        "→ 返回各章节 heading + 关键词 + 偏移量"
    )

    async def execute(
        self, text: str | None = None, path: str | None = None,
        max_sections: int = 10, mode: str = "auto", **kwargs: Any,
    ) -> str:
        if not text and not path:
            return "Provide either text or path."
        if text and path:
            return "Provide either text or path, not both."

        if path:
            from nanobot.agent.tools.filesystem.filesystem_base import _resolve_path
            try:
                resolved = _resolve_path(path, self._workspace, self._allowed_dir)
                raw = resolved.read_bytes()
                if len(raw) > _MAX_TEXT_BYTES:
                    return f"File too large ({len(raw)} bytes). Maximum is {_MAX_TEXT_BYTES // (1024 * 1024)} MB."
                text = raw.decode("utf-8")
            except Exception as e:
                return f"Cannot read file: {e}"

        if not text or not text.strip():
            return "Empty text."

        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            return (
                f"text too large ({len(text)} chars / "
                f"{len(text.encode('utf-8'))} bytes). "
                f"Maximum is {_MAX_TEXT_BYTES // (1024 * 1024)} MB."
            )

        if not text.strip():
            return "Empty text."

        sections = _detect_sections(text, max_sections, mode)

        lines = text.split("\n")
        estimated_tokens = int(len(text) * 0.38)

        out = [
            f"**Document overview** — {len(text)} chars, ~{estimated_tokens} tokens, "
            f"{len(lines)} lines, {len(sections)} section(s)\n"
        ]

        for i, sec in enumerate(sections):
            heading = sec.get("heading", "") or f"Section {i + 1}"
            score = sec.get("score", 0)
            keywords = sec.get("keywords", [])
            rep = sec.get("representative", "")
            start = sec.get("start_char", 0)
            length = sec.get("end_char", 0) - start

            out.append(f"### {heading}")
            out.append(f"> Offset {start}–{start + length} ({length} chars)")
            if score:
                out.append(f"> Coherence: {score:.2f}")
            if keywords:
                out.append(f"> Keywords: {', '.join(keywords[:8])}")
            if rep:
                truncated = rep if len(rep) <= 250 else rep[:247] + "..."
                out.append(f"> Representative: _{truncated}_")

        return "\n".join(out)


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------


def _detect_sections(
    text: str, max_sections: int, mode: str = "auto",
) -> list[dict[str, Any]]:
    """Detect sections by structure first, fall back to embedding-based.

    *mode*: ``"auto"`` (default), ``"structure"``, or ``"semantic"``.
    """
    if mode == "structure":
        sections = _try_structured(text)
        return (sections or _fallback_sections(text, max_sections))[:max_sections]

    if mode == "semantic":
        model = get_model()
        if model:
            sections = segment_unstructured(text, model, max_sections=max_sections)
            for i, sec in enumerate(sections):
                sec["heading"] = _auto_heading(sec, i)
            return sections[:max_sections]
        return _fallback_sections(text, max_sections)[:max_sections]

    # auto: try structure first
    sections = _try_structured(text)
    if sections:
        return sections[:max_sections]

    model = get_model()
    if model:
        sections = segment_unstructured(text, model, max_sections=max_sections)
        for i, sec in enumerate(sections):
            sec["heading"] = _auto_heading(sec, i)
        return sections

    return _fallback_sections(text, max_sections)


def _try_structured(text: str) -> list[dict[str, Any]] | None:
    """Try extracting sections using structural patterns.

    Returns ``None`` if no structure found (caller should fall back).
    """
    lines = text.split("\n")
    line_offsets: list[int] = [0]
    for line in lines[:-1]:
        line_offsets.append(line_offsets[-1] + len(line) + 1)

    # 1. Markdown headings (# or ## or ###)
    heading_indices: list[int] = []
    heading_texts: list[str] = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            heading_indices.append(i)
            heading_texts.append(f"{m.group(1)} {m.group(2)}")

    # Strip markdown heading markers for display
    def _clean_h(text: str) -> str:
        return re.sub(r"^#+\s*", "", text)

    if heading_indices:
        sections: list[dict[str, Any]] = []
        prev = 0
        hi_idx = 0
        for b in heading_indices:
            if hi_idx > 0:
                sec_text = "\n".join(lines[prev:b]).strip()
                sec_start = line_offsets[prev]
                prev_line = b - 1
                end_of_prev_line = line_offsets[prev_line] + len(lines[prev_line]) if prev < b else line_offsets[prev]
                sections.append(_build_section_info(sec_text, _clean_h(heading_texts[hi_idx - 1]), sec_start, end_of_prev_line))
            prev = b
            hi_idx += 1
        sec_text = "\n".join(lines[prev:]).strip()
        sec_start = line_offsets[prev]
        sec_end = line_offsets[-1] + len(lines[-1]) if lines else 0
        sections.append(_build_section_info(sec_text, _clean_h(heading_texts[-1]) if heading_texts else "", sec_start, sec_end))
        return sections

    # 2. JSON top-level keys
    json_candidates = _try_json_structure(text)
    if json_candidates:
        return json_candidates

    # 3. Separator lines (--- or ===)
    sep_indices = [i for i, line in enumerate(lines) if re.match(r"^-{3,}|={3,}$", line.strip())]
    if sep_indices:
        sections = []
        prev = 0
        for b in sep_indices:
            sec_text = "\n".join(lines[prev:b]).strip()
            if sec_text:
                sec_start = line_offsets[prev]
                prev_line = b - 1
                end_of_prev = line_offsets[prev_line] + len(lines[prev_line]) if prev < b else line_offsets[prev]
                first_line = sec_text.split("\n")[0].strip()
                sections.append(_build_section_info(sec_text, first_line, sec_start, end_of_prev))
            prev = b + 1
        sec_text = "\n".join(lines[prev:]).strip()
        if sec_text:
            sec_start = line_offsets[prev]
            sec_end = line_offsets[-1] + len(lines[-1]) if lines else 0
            sections.append(_build_section_info(sec_text, "Misc", sec_start, sec_end))
        return sections

    return None


def _try_json_structure(text: str) -> list[dict[str, Any]] | None:
    """Detect JSON top-level keys as sections."""
    text_stripped = text.strip()
    try:
        data = json.loads(text_stripped)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    sections: list[dict[str, Any]] = []
    for key, value in data.items():
        if isinstance(value, str):
            preview = value
        elif isinstance(value, (list, dict)):
            preview = json.dumps(value, ensure_ascii=False)[:200]
        else:
            preview = str(value)
        sec_text = f"{key}: {preview}"
        key_json = json.dumps(key, ensure_ascii=False)
        sections.append({
            "start_char": serialized.find(key_json),
            "end_char": 0,
            "text": sec_text,
            "heading": key,
            "representative": preview[:200],
            "keywords": [k["term"] for k in extract_keywords(sec_text, top_n=5)],
            "score": 0,
        })

    # Fix end_char for all but last
    for i in range(len(sections) - 1):
        sections[i]["end_char"] = sections[i + 1]["start_char"]
    if sections:
        sections[-1]["end_char"] = len(serialized)

    return sections


def _build_section_info(
    text: str, heading: str, start_char: int, end_char: int,
) -> dict[str, Any]:
    """Build section info dict from section text."""
    model = get_model()
    kw = extract_keywords(text, top_n=5)
    rep = ""
    if model and text:
        try:
            rep = _find_representative(text, model)
        except Exception:
            first_lines = [s for s in text.split("\n") if s.strip()]
            if first_lines:
                rep = first_lines[0][:200]
    elif text:
        first = text.split("\n")[0].strip()
        if first:
            rep = first[:200]
    return {
        "start_char": start_char,
        "end_char": end_char,
        "text": text,
        "heading": heading,
        "representative": rep,
        "keywords": [k["term"] for k in kw],
        "score": 0,
    }


def _auto_heading(sec: dict[str, Any], idx: int) -> str:
    """Auto-generate a heading for an unstructured section."""
    kw = sec.get("keywords", [])
    rep = sec.get("representative", "")
    if kw:
        return f"Topic: {', '.join(kw[:3])}"
    if rep:
        return rep[:40]
    return f"Section {idx + 1}"


def _fallback_sections(text: str, max_sections: int) -> list[dict[str, Any]]:
    """Fallback: split by double newlines when no model is available."""
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    if not paragraphs:
        return []
    n_per = max(1, len(paragraphs) // max_sections)
    sections: list[dict[str, Any]] = []
    for i in range(0, len(paragraphs), n_per):
        chunk = "\n\n".join(paragraphs[i:i + n_per])
        start = text.find(chunk[:50])
        if start == -1:
            start = 0
        end = start + len(chunk)
        kw = extract_keywords(chunk, top_n=5)
        sections.append({
            "start_char": start,
            "end_char": end,
            "text": chunk[:200],
            "heading": f"Section {len(sections) + 1}",
            "representative": chunk.split("\n")[0].strip()[:200],
            "keywords": [k["term"] for k in kw],
            "score": 0,
        })
    return sections[:max_sections]
