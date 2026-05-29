"""Memory vector index using FAISS for retrieval."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from loguru import logger


class MemoryVectorIndex:
    """FAISS-based vector index for memory retrieval.

    Uses sentence-transformers for embedding (optional dependency).
    Gracefully degrades when sentence-transformers is not installed.
    """

    _MODEL_NAME = "BAAI/bge-small-zh-v1.5"
    _INDEX_FILE = "index.faiss"
    _CHUNKS_FILE = "chunks.json"

    def __init__(self, memory_dir: Path, index_dir: str = ".vector_index") -> None:
        self._memory_dir = memory_dir
        self._index_dir = memory_dir / index_dir
        self._model: Any = None  # lazy-loaded
        self._model_lock = threading.Lock()
        self._index: Any = None  # faiss Index
        self._chunks: list[dict[str, Any]] = []

    # -- embedding model -------------------------------------------------------

    def _load_model(self) -> bool:
        """Lazy-load sentence-transformers model. Returns True if loaded.

        Also checks index dimension on load; invalidates mismatched index.
        """
        if self._model is not None:
            self._check_index_dimension()
            return True
        with self._model_lock:
            if self._model is not None:
                self._check_index_dimension()
                return True
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._MODEL_NAME)
                self._check_index_dimension()
                return True
            except ImportError:
                return False

    def _check_index_dimension(self) -> None:
        """Invalidate _index if its dimension doesn't match the model."""
        if self._index is None:
            return
        dim = self._model.get_sentence_embedding_dimension()
        if dim != self._index.d:
            logger.warning(
                "Index dimension ({}) differs from model dimension ({}), "
                "discarding old index; rebuild on next write",
                self._index.d, dim,
            )
            self._index = None

    # -- chunking --------------------------------------------------------------

    @staticmethod
    def _chunk_markdown(content: str, source: str) -> list[dict[str, Any]]:
        """Split markdown into chunks by ``##`` headings, max 1000 chars."""
        chunks: list[dict[str, Any]] = []
        lines = content.split("\n")
        current_section = ""
        current_lines: list[str] = []

        for line in lines:
            if line.startswith("## ") and current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    chunks.append({"text": text, "source": source, "heading": current_section})
                current_lines = [line]
                current_section = line.lstrip("# ").strip()
            else:
                current_lines.append(line)

        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                chunks.append({"text": text, "source": source, "heading": current_section})

        # Split oversized chunks
        final: list[dict[str, Any]] = []
        for chunk in chunks:
            if len(chunk["text"]) > 1000:
                parts = MemoryVectorIndex._split_text(chunk["text"], 1000)
                for i, part in enumerate(parts):
                    final.append({
                        "text": part,
                        "source": chunk["source"],
                        "heading": f"{chunk['heading']} (part {i + 1})",
                    })
            else:
                final.append(chunk)

        return final

    @staticmethod
    def _split_text(text: str, max_chars: int) -> list[str]:
        """Split text at paragraph boundaries, max *max_chars* per chunk."""
        chunks: list[str] = []
        remaining = text
        while len(remaining) > max_chars:
            split_at = remaining.rfind("\n\n", 0, max_chars)
            if split_at == -1:
                split_at = remaining.rfind("\n", 0, max_chars)
            if split_at == -1:
                split_at = max_chars
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    # -- build -----------------------------------------------------------------

    def build_from_files(self, file_texts: dict[str, str]) -> None:
        """Build index from categorized memory files.

        *file_texts* maps relative source paths (e.g. ``conversations/index.md``)
        to their full text content.
        """
        self._chunks = []
        self._index = None

        if not self._load_model():
            logger.warning("Vector search disabled — pip install sentence-transformers to enable semantic memory retrieval")
            return

        chunks: list[dict[str, Any]] = []
        for source, content in file_texts.items():
            if content.strip():
                chunks.extend(self._chunk_markdown(content, source))

        if not chunks:
            return

        import faiss
        import numpy as np

        texts = [c["text"] for c in chunks]
        embeddings = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

        index = faiss.IndexHNSWFlat(embeddings.shape[1], 32, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.ef_construction = 80
        index.add(np.array(embeddings, dtype=np.float32))

        self._index = index
        self._chunks = chunks

    # -- hybrid search (vector + keyword + RRF) --------------------------------

    @staticmethod
    def _extract_terms(query: str) -> set[str]:
        """Extract meaningful search terms from query for keyword matching.

        Handles English words (>=2 chars) and Chinese bigrams.
        """
        import re

        terms: set[str] = set()
        for part in query.split():
            terms.update(w.lower() for w in re.findall(r"[a-zA-Z]{2,}", part))
            cjk = re.findall(r"[一-鿿]", part)
            for i in range(len(cjk) - 1):
                terms.add(cjk[i] + cjk[i + 1])
        return terms

    def _keyword_rank(self, terms: set[str]) -> dict[int, int]:
        """Score all chunks by keyword hit count. Returns {chunk_idx: rank}."""
        if not terms:
            return {}

        scored: list[tuple[int, int]] = []
        for idx, chunk in enumerate(self._chunks):
            text = chunk.get("text", "").lower()
            count = sum(1 for t in terms if t in text)
            if count > 0:
                scored.append((idx, count))

        scored.sort(key=lambda x: -x[1])
        return {idx: rank for rank, (idx, _) in enumerate(scored)}

    def search(self, query: str, k: int = 5, min_score: float = 0.3) -> list[dict[str, Any]]:
        """Search memory chunks by *query* using hybrid vector + keyword search.

        Uses Reciprocal Rank Fusion (RRF) to merge FAISS vector search results
        with keyword term matching.  Returns up to *k* results with ``source``,
        ``heading``, ``text``, and ``score`` keys.
        """
        if not self._chunks:
            return []
        if not self._load_model():
            return []
        if self._index is None:
            return []

        import numpy as np

        query_vec = self._model.encode([query], normalize_embeddings=True)

        if hasattr(self._index, "hnsw"):
            self._index.hnsw.ef_search = 40

        # Fetch extra candidates from FAISS for better RRF fusion
        faiss_k = min(k * 3, len(self._chunks))
        scores, indices = self._index.search(
            np.array(query_vec, dtype=np.float32), faiss_k,
        )

        # Build FAISS result list with vector rank
        faiss_results: list[dict[str, Any]] = []
        for v_rank, (score, idx) in enumerate(zip(scores[0], indices[0])):
            if idx < 0 or idx >= len(self._chunks):
                continue
            if score < min_score:
                continue
            chunk = self._chunks[idx]
            faiss_results.append({
                "source": chunk["source"],
                "heading": chunk.get("heading", ""),
                "text": chunk["text"],
                "_vec_score": float(score),
                "_vec_rank": v_rank,
                "_chunk_idx": idx,
            })

        # Keyword ranking across all chunks
        terms = self._extract_terms(query)
        kw_rank = self._keyword_rank(terms)

        if not kw_rank:
            # No keyword terms or matches — fall back to pure FAISS
            for r in faiss_results:
                r["score"] = r.pop("_vec_score")
                del r["_vec_rank"], r["_chunk_idx"]
            return faiss_results[:k]

        # RRF fusion: 1/(k + rank) for each strategy
        RRF_K = 61  # gbrain uses k=60; +1 for 0-indexed ranks

        seen: set[int] = set()
        fused: list[dict[str, Any]] = []

        for r in faiss_results:
            ci = r["_chunk_idx"]
            vr = r["_vec_rank"]
            kr = kw_rank.get(ci)
            rrf = 1.0 / (RRF_K + vr)
            if kr is not None:
                rrf += 1.0 / (RRF_K + kr)
            r["score"] = rrf
            del r["_vec_rank"], r["_chunk_idx"], r["_vec_score"]
            fused.append(r)
            seen.add(ci)

        # Add keyword-only results (missed by vector)
        for ci, kr in sorted(kw_rank.items(), key=lambda x: x[1]):
            if ci in seen:
                continue
            chunk = self._chunks[ci]
            fused.append({
                "source": chunk["source"],
                "heading": chunk.get("heading", ""),
                "text": chunk["text"],
                "score": 1.0 / (RRF_K + kr),
            })
            if len(fused) >= k + len(faiss_results):
                break

        fused.sort(key=lambda x: -x["score"])
        return fused[:k]

    # -- persistence -----------------------------------------------------------

    def save(self) -> None:
        """Persist FAISS index and chunk metadata to disk."""
        self._index_dir.mkdir(parents=True, exist_ok=True)

        if self._index is not None:
            import faiss
            faiss.write_index(self._index, str(self._index_dir / self._INDEX_FILE))

        (self._index_dir / self._CHUNKS_FILE).write_text(
            json.dumps(self._chunks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self) -> bool:
        """Load persisted FAISS index and chunks. Returns True on success."""
        chunks_path = self._index_dir / self._CHUNKS_FILE
        if not chunks_path.exists():
            return False

        try:
            self._chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load memory index chunks")
            return False

        index_path = self._index_dir / self._INDEX_FILE
        if index_path.exists():
            try:
                import faiss

                self._index = faiss.read_index(str(index_path))
            except Exception:
                logger.warning("Failed to load FAISS index at {} — will rebuild", index_path)
                self._index = None

        # Check dimension when model is already loaded (e.g. restart without rebuild)
        if self._model is not None and self._index is not None:
            self._check_index_dimension()

        return self._index is not None
