"""Shared utilities for semantic search — embedding model, chunking, similarity."""

from __future__ import annotations

import re
import threading
from collections import Counter
from typing import Any

from loguru import logger

# numpy is imported lazily inside functions that need it (not available on all setups)

_MODEL: Any = None
_MODEL_LOCK = threading.Lock()
_MODEL_PATH = Path(__file__).resolve().parent.parent.parent / "models" / "bge-small-zh-v1.5"


def get_model() -> Any:
    """Lazy-load and return the sentence-transformers model, or None if unavailable."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            from sentence_transformers import SentenceTransformer

            _MODEL = SentenceTransformer(str(_MODEL_PATH))
            return _MODEL
        except ImportError:
            return None


def chunk_text(
    text: str,
    max_chars: int = 500,
    overlap: int = 50,
) -> list[dict[str, Any]]:
    """Split *text* into overlapping chunks, tracking character positions.

    Returns list of dicts with keys: ``text``, ``start_char``, ``end_char``.
    Offsets are relative to the original *text*.
    If *text* is empty, returns ``[]``.
    """
    if not text:
        return []

    lines = text.split("\n")
    # char offset of each line from start of text
    line_offsets: list[int] = [0]
    for line in lines[:-1]:
        line_offsets.append(line_offsets[-1] + len(line) + 1)  # +1 for \n

    # -- find ## heading boundaries to group into sections -------------------
    heading_lines = [
        i for i, line in enumerate(lines) if line.startswith("## ") and i > 0
    ]

    # Build (base_offset, section_text) pairs
    sections: list[tuple[int, str]] = []
    prev = 0
    for b in heading_lines:
        sections.append(
            (line_offsets[prev], "\n".join(lines[prev:b]))
        )
        prev = b
    sections.append(
        (line_offsets[prev], "\n".join(lines[prev:]))
    )

    chunks: list[dict[str, Any]] = []
    for base_offset, sec in sections:
        _split_section(sec, max_chars, overlap, chunks, base_offset)
    return chunks


def _split_section(
    text: str,
    max_chars: int,
    overlap: int,
    out: list[dict[str, Any]],
    base_offset: int = 0,
) -> None:
    """Split a single section into chunks, appending to *out*."""
    paragraphs = re.split(r"\n\s*\n", text)
    current = ""
    current_start = base_offset
    char_pos = base_offset  # char position relative to the full text

    for para in paragraphs:
        para = para.strip()
        if not para:
            char_pos += 2
            continue

        # -- paragraph exceeds max_chars alone -> split in place --------------
        if len(para) > max_chars:
            if current:
                out.append({
                    "text": current.strip(),
                    "start_char": current_start,
                    "end_char": char_pos,
                })
                current = ""
            _split_long_text(para, max_chars, overlap, char_pos, out)
            char_pos += len(para) + 2
            continue

        # -- normal case: try to append to current chunk --------------------
        sep_len = 2 if current else 0
        if current and len(current) + sep_len + len(para) > max_chars:
            out.append({
                "text": current.strip(),
                "start_char": current_start,
                "end_char": char_pos,
            })
            # start new chunk with overlap tail
            tail = _overlap_tail(current, overlap)
            current = (tail + "\n\n" + para) if tail else para
            current_start = char_pos - len(tail) if tail else char_pos
        else:
            if not current:
                current_start = char_pos
            current = (current + "\n\n" + para) if current else para

        char_pos += len(para) + 2

    if current:
        out.append({
            "text": current.strip(),
            "start_char": current_start,
            "end_char": char_pos,
        })


def _split_long_text(
    text: str,
    max_chars: int,
    overlap: int,
    base_offset: int,
    out: list[dict[str, Any]],
) -> None:
    """Split a single long block without heading/paragraph breaks."""
    min_split = max_chars // 3  # ensure no tiny chunks
    remaining = text
    offset = base_offset
    while remaining:
        if len(remaining) <= max_chars:
            out.append({
                "text": remaining.strip(),
                "start_char": offset,
                "end_char": offset + len(remaining),
            })
            break
        split_at = _find_split(remaining[:max_chars])
        if split_at < min_split:
            split_at = min_split
        chunk_text = remaining[:split_at].strip()
        if chunk_text:
            out.append({
                "text": chunk_text,
                "start_char": offset,
                "end_char": offset + split_at,
            })
        advance = max(split_at - overlap, split_at // 4, 1)
        offset += advance
        remaining = remaining[advance:].strip()


def _find_split(text: str) -> int:
    """Find the best split position within *text* (sentence -> line -> hard)."""
    for sep in ("。", ". ", "！", "？", "\n"):
        idx = text.rfind(sep, 0, len(text))
        if idx != -1:
            return idx + len(sep)
    return len(text)


def _overlap_tail(text: str, overlap_chars: int) -> str:
    """Return the trailing *overlap_chars* from *text*, preferably at a boundary."""
    if len(text) <= overlap_chars:
        return text
    tail = text[-overlap_chars:]
    for sep in ("。", "！", "？", "\n"):
        idx = tail.find(sep)
        if idx != -1:
            return tail[idx + 1 :].strip()
    return tail


def compute_similarity(
    query: str,
    chunks: list[dict[str, Any]],
    model: Any,
    k: int = 5,
) -> list[dict[str, Any]]:
    """Score *chunks* against *query* using cosine similarity.

    Returns top-*k* chunks with a ``score`` key appended (descending).
    Chunks with scores below 0.1 are filtered out.
    Returns ``[]`` when the model is unavailable.
    """
    if not chunks or model is None:
        return []
    import numpy as np

    texts = [c["text"] for c in chunks]
    query_vec = model.encode([query], normalize_embeddings=True)
    chunk_vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

    scores = np.dot(chunk_vecs, query_vec[0])

    top_k = min(k, len(chunks))
    top_indices = np.argpartition(scores, -top_k)[-top_k:]
    top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

    results: list[dict[str, Any]] = []
    for idx in top_indices:
        score = float(scores[idx])
        if score < 0.1:
            continue
        result = dict(chunks[idx])
        result["score"] = score
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Keyword extraction (lightweight, no external deps)
# ---------------------------------------------------------------------------

_STOPS: frozenset[str] = frozenset({
    # Chinese
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "来", "为", "等", "可以", "这个", "那个", "什么", "怎么", "如何",
    "但", "但是", "如果", "因为", "所以", "然后", "就是", "只是",
    "其", "中", "而", "从", "把", "被", "让", "对", "与", "以", "及",
    "或", "或者", "之", "于", "很", "更", "最", "所", "得",
    "我们", "你们", "他们", "已经", "可以", "需要", "使用", "通过",
    "进行", "一些", "不是", "还是", "没有", "可能", "应该",
    "将", "并", "且", "还", "又", "再",
    # English
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "can", "could", "should", "may", "might", "it", "its", "this",
    "that", "these", "those", "i", "you", "he", "she", "we", "they",
    "me", "him", "her", "us", "them", "my", "your", "his", "their",
    "not", "no", "nor", "so", "if", "then", "than", "as", "just",
    "about", "also", "very", "too", "only", "into", "over", "such",
    "each", "well", "more", "most", "some", "any", "all", "both",
    "other", "own", "which", "what", "when", "where", "how",
    "while", "been", "being", "having", "doing",
})


def _tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text, returning candidate terms."""
    tokens: list[str] = []
    # Extract English words
    for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_\-+#]*", text):
        if word.lower() not in _STOPS and len(word) >= 2:
            tokens.append(word)
    # Chinese character bigrams (skip if both chars are stop chars)
    for i in range(len(text) - 1):
        pair = text[i : i + 2]
        if re.match(r"[一-鿿]{2}", pair):
            tokens.append(pair)
    return tokens


def extract_keywords(text: str, top_n: int = 6) -> list[dict[str, Any]]:
    """Extract top *top_n* keywords from *text* by frequency.

    Returns list of ``{"term": str, "count": int}`` sorted by count descending.
    """
    tokens = _tokenize(text)
    if not tokens:
        return []
    counts = Counter(tokens)
    total = sum(counts.values())
    results = []
    for term, count in counts.most_common(top_n):
        results.append({"term": term, "count": count, "freq": round(count / total, 3)})
    return results


# ---------------------------------------------------------------------------
# Unstructured topic segmentation
# ---------------------------------------------------------------------------


def segment_unstructured(
    text: str,
    model: Any,
    max_sections: int = 5,
    window_chars: int = 150,
) -> list[dict[str, Any]]:
    """Segment unstructured *text* into topic sections using embedding similarity.

    Returns list of section info dicts with keys:
    ``start_char``, ``end_char``, ``text``, ``representative``,
    ``keywords``, ``score`` (internal consistency).
    """
    if not text or model is None:
        return [{"start_char": 0, "end_char": len(text), "text": text,
                 "representative": "", "keywords": [], "score": 0.0}]
    import numpy as np

    # -- split into small window-sized blocks with ~50% overlap --------------
    step = window_chars // 2
    blocks: list[dict[str, Any]] = []
    pos = 0
    while pos < len(text):
        block_text = text[pos: pos + window_chars].strip()
        if block_text:
            blocks.append({"text": block_text, "start_char": pos})
        pos += step
    if not blocks:
        return []

    # -- embed all blocks ----------------------------------------------------
    block_texts = [b["text"] for b in blocks]
    vecs = model.encode(block_texts, normalize_embeddings=True, show_progress_bar=False)

    # -- detect boundaries by adjacent similarity drops ----------------------
    sims: list[float] = []
    for i in range(len(blocks) - 1):
        sims.append(float(np.dot(vecs[i], vecs[i + 1])))

    # Find valley points (local minima) as topic boundaries
    valleys: list[int] = []
    for i in range(1, len(sims) - 1):
        if sims[i] < 0.5 and sims[i] < sims[i - 1] and sims[i] <= sims[i + 1]:
            valleys.append(i + 1)  # boundary after block i

    # If no clear valleys, just split at the largest drops
    if not valleys:
        n_drops = min(max_sections - 1, len(sims))
        if n_drops > 0:
            valleys = sorted(np.argpartition(sims, n_drops)[:n_drops])
            valleys = sorted(set(v + 1 for v in valleys if sims[v] < 0.6))

    # -- build sections from block boundaries -------------------------------
    boundaries = [0] + valleys + [len(blocks)]
    sections: list[dict[str, Any]] = []
    for si in range(len(boundaries) - 1):
        start_idx = boundaries[si]
        end_idx = boundaries[si + 1]
        section_blocks = blocks[start_idx:end_idx]
        if not section_blocks:
            continue
        sec_start = section_blocks[0]["start_char"]
        sec_end = (section_blocks[-1]["start_char"] +
                   len(section_blocks[-1]["text"]))
        sec_text = text[sec_start:sec_end].strip()
        if not sec_text:
            continue

        # section centroid embedding
        sec_vecs = vecs[start_idx:end_idx]
        centroid = np.mean(sec_vecs, axis=0)
        centroid = centroid / np.linalg.norm(centroid)

        # internal consistency
        scores = [float(np.dot(v, centroid)) for v in sec_vecs]
        consistency = float(np.mean(scores))

        # representative sentence
        rep = _find_representative(sec_text, model)

        # keywords
        kw = extract_keywords(sec_text, top_n=5)

        sections.append({
            "start_char": sec_start,
            "end_char": sec_end,
            "text": sec_text[:200],
            "representative": rep,
            "keywords": [k["term"] for k in kw],
            "score": round(consistency, 3),
        })

    # Limit to max_sections
    if len(sections) > max_sections:
        sections = _merge_smallest_sections(sections, max_sections)

    return sections


def _find_representative(text: str, model: Any) -> str:
    """Find the single sentence most representative of *text*."""
    import numpy as np
    sentences = re.split(r"(?<=[。！？.!?\n])\s*", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
    if not sentences:
        return text[:200].strip()
    if len(sentences) == 1:
        return sentences[0][:200]

    try:
        vecs = model.encode(sentences, normalize_embeddings=True, show_progress_bar=False)
        centroid = np.mean(vecs, axis=0)
        centroid = centroid / np.linalg.norm(centroid)
        scores = np.dot(vecs, centroid)
        return sentences[int(np.argmax(scores))][:200]
    except Exception:
        logger.warning("Failed to compute centroid summary", exc_info=True)
        return sentences[0][:200]


def _merge_smallest_sections(
    sections: list[dict[str, Any]],
    target: int,
) -> list[dict[str, Any]]:
    """Greedily merge the smallest adjacent section pair until *target* remains."""
    while len(sections) > target:
        # find the smallest section
        sizes = [s["end_char"] - s["start_char"] for s in sections]
        smallest = min(range(len(sections)), key=lambda i: sizes[i])
        # merge with neighbor (prefer smaller neighbor)
        if smallest == 0:
            partner = 1
        elif smallest == len(sections) - 1:
            partner = smallest - 1
        elif sizes[smallest - 1] <= sizes[smallest + 1]:
            partner = smallest - 1
        else:
            partner = smallest + 1
        lo, hi = min(smallest, partner), max(smallest, partner)
        merged = {
            "start_char": sections[lo]["start_char"],
            "end_char": sections[hi]["end_char"],
            "text": "",
            "representative": sections[lo].get("representative", ""),
            "keywords": list(set(
                sections[lo].get("keywords", []) +
                sections[hi].get("keywords", [])
            ))[:6],
            "score": (sections[lo]["score"] + sections[hi]["score"]) / 2,
        }
        sections[lo:hi + 1] = [merged]
    return sections
