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

    # --------------------------------------------------------------------------
    # Embedding model
    # --------------------------------------------------------------------------

    def _load_model(self) -> bool:
        """Lazy-load sentence-transformers model. Returns True if loaded."""
        if self._model is not None:
            return True
        with self._model_lock:
            if self._model is not None:
                return True
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._MODEL_NAME)
                return True
            except ImportError:
                return False

    # --------------------------------------------------------------------------
    # Chunking
    # --------------------------------------------------------------------------

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

    # --------------------------------------------------------------------------
    # Build
    # --------------------------------------------------------------------------

    def build_from_files(self, file_texts: dict[str, str]) -> None:
        """Build index from categorized memory files.

        *file_texts* maps relative source paths (e.g. ``conversations/index.md``)
        to their full text content.

        Encodes texts with sentence-transformers, builds a FAISS index,
        and persists to disk.
        """
        self._chunks = []
        self._index = None

        if not self._load_model():
            logger.warning(
                "Vector search disabled — pip install sentence-transformers to enable "
                "semantic memory retrieval",
            )
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

        # Build FAISS index
        index = faiss.IndexHNSWFlat(embeddings.shape[1], 32, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.ef_construction = 80
        index.add(np.array(embeddings, dtype=np.float32))

        self._index = index
        self._chunks = chunks

        logger.info("Built FAISS index with {} chunks from {} source files", len(chunks), len(file_texts))

    # --------------------------------------------------------------------------
    # Hybrid search (vector + keyword + RRF)
    # --------------------------------------------------------------------------

    @staticmethod
    def _extract_terms(query: str) -> set[str]:
        """Extract meaningful search terms from query for keyword matching."""
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

    def _keyword_search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Pure keyword-based search. Used as fallback when no embeddings available."""
        if not self._chunks:
            return []

        terms = self._extract_terms(query)
        if not terms:
            return []

        scored: list[tuple[int, int]] = []
        for idx, chunk in enumerate(self._chunks):
            text = chunk.get("text", "").lower()
            count = sum(1 for t in terms if t in text)
            if count > 0:
                scored.append((idx, count))

        scored.sort(key=lambda x: -x[1])
        results = []
        for rank, (idx, _score) in enumerate(scored[:k]):
            chunk = self._chunks[idx]
            results.append({
                "source": chunk["source"],
                "heading": chunk.get("heading", ""),
                "text": chunk["text"],
                "score": 1.0 / (61 + rank),
            })
        return results

    def search(self, query: str, k: int = 5, min_score: float = 0.3) -> list[dict[str, Any]]:
        """Search memory chunks by *query* using hybrid vector + keyword search.

        Strategy:
          1. If FAISS index available → semantic similarity search with RRF fusion
          2. Otherwise → pure keyword search (fallback)

        Returns up to *k* results with ``source``, ``heading``, ``text``, and ``score`` keys.
        """
        if not self._chunks:
            return []

        model_loaded = self._load_model()

        if model_loaded and self._index is not None and self._index.ntotal > 0:
            return self._faiss_search(query, k, min_score)

        logger.debug("No FAISS index — falling back to keyword search")
        return self._keyword_search(query, k)

    def _faiss_search(self, query: str, k: int, min_score: float) -> list[dict[str, Any]]:
        """Run FAISS + keyword hybrid search with RRF fusion."""
        import numpy as np

        query_vec = self._model.encode([query], normalize_embeddings=True)

        if hasattr(self._index, "hnsw"):
            self._index.hnsw.ef_search = 40

        faiss_k = min(k * 3, self._index.ntotal)
        scores, indices = self._index.search(
            np.array(query_vec, dtype=np.float32), faiss_k,
        )

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

        terms = self._extract_terms(query)
        kw_rank = self._keyword_rank(terms)

        if not kw_rank:
            for r in faiss_results:
                r["score"] = r.pop("_vec_score")
                del r["_vec_rank"], r["_chunk_idx"]
            return faiss_results[:k]

        RRF_K = 61

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

    # --------------------------------------------------------------------------
    # Persistence
    # --------------------------------------------------------------------------

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
        """Load persisted FAISS index and chunks from disk. Returns True on success.

        Loads both the chunk metadata and FAISS index file synchronously
        (both are small reads). The sentence-transformers model is loaded
        lazily on the first call to :meth:`search`.
        """
        chunks_path = self._index_dir / self._CHUNKS_FILE
        if not chunks_path.exists():
            return False

        try:
            self._chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load memory index chunks")
            return False

        # Load FAISS index if it exists (fast — just a file read + deserialize)
        index_path = self._index_dir / self._INDEX_FILE
        if index_path.exists():
            try:
                import faiss

                self._index = faiss.read_index(str(index_path))
            except Exception:
                logger.warning("Failed to load FAISS index at {}", index_path)

        return True
