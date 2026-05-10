"""Memory vector index using FAISS for retrieval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


class MemoryVectorIndex:
    """FAISS-based vector index for memory retrieval.

    Uses sentence-transformers for embedding (optional dependency).
    Gracefully degrades when sentence-transformers is not installed.
    """

    _INDEX_DIR = ".vector_index"
    _INDEX_FILE = "index.faiss"
    _CHUNKS_FILE = "chunks.json"

    def __init__(self, memory_dir: Path) -> None:
        self._memory_dir = memory_dir
        self._index_dir = memory_dir / self._INDEX_DIR
        self._model: Any = None  # lazy-loaded
        self._index: Any = None  # faiss Index
        self._chunks: list[dict[str, Any]] = []

    # -- embedding model -------------------------------------------------------

    def _load_model(self) -> bool:
        """Lazy-load sentence-transformers model. Returns True if loaded."""
        if self._model is not None:
            return True
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer("all-MiniLM-L6-v2")
            return True
        except ImportError:
            return False

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
            logger.info("Vector search unavailable (sentence-transformers not installed)")
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

        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(np.array(embeddings, dtype=np.float32))

        self._index = index
        self._chunks = chunks

    # -- search ----------------------------------------------------------------

    def search(self, query: str, k: int = 5, min_score: float = 0.3) -> list[dict[str, Any]]:
        """Search memory chunks by *query*.

        Returns up to *k* results with ``source``, ``heading``, ``text``,
        and ``score`` keys.  Results below *min_score* are discarded.
        """
        if self._index is None or not self._chunks:
            return []
        if not self._load_model():
            return []

        import numpy as np

        query_vec = self._model.encode([query], normalize_embeddings=True)
        scores, indices = self._index.search(
            np.array(query_vec, dtype=np.float32),
            k=min(k, len(self._chunks)),
        )

        results: list[dict[str, Any]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._chunks):
                continue
            if score < min_score:
                continue
            chunk = self._chunks[idx]
            results.append({
                "source": chunk["source"],
                "heading": chunk.get("heading", ""),
                "text": chunk["text"],
                "score": float(score),
            })

        return results

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
                logger.warning("Failed to load FAISS index")
                self._index = None

        return self._index is not None
