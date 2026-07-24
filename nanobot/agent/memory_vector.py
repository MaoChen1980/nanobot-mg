"""Memory vector index using FAISS for retrieval.

Supports incremental indexing via IndexFlatIP + IndexIDMap with
file_map.json change tracking.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from loguru import logger

_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "bge-small-zh-v1.5"


class MemoryVectorIndex:
    """FAISS-based vector index for memory retrieval.

    Uses sentence-transformers for embedding (optional dependency).
    Gracefully degrades when sentence-transformers is not installed.
    Supports incremental indexing via mtime-based change tracking.
    """
    _INDEX_FILE = "index.faiss"
    _INDEX_BAK = "index.faiss.bak"
    _CHUNKS_FILE = "chunks.json"
    _FILE_MAP = "file_map.json"

    def __init__(self, memory_dir: Path, index_dir: str = ".vector_index") -> None:
        self._memory_dir = memory_dir
        self._index_dir = memory_dir / index_dir
        self._model: Any = None
        self._model_lock = threading.Lock()
        self._index: Any = None
        # chunks indexed by chunk_id (list, may contain None for deleted entries)
        self._chunks: list[dict[str, Any] | None] = []

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

                # Force CPU to avoid MPS OOM on Apple Silicon (MPS has ~9 GiB limit;
                # loading the model + running inference during cron jobs exhausts it).
                self._model = SentenceTransformer(str(_MODEL_PATH), device="cpu")
                return True
            except Exception:
                logger.warning("Failed to load SentenceTransformer model", exc_info=True)
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
    # File map (change tracking for incremental indexing)
    # --------------------------------------------------------------------------

    def _load_file_map(self) -> dict[str, Any]:
        """Load file_map.json (mtime + chunk_ids per file)."""
        path = self._index_dir / self._FILE_MAP
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Failed to load file_map.json, starting fresh", exc_info=True)
        return {"next_id": 0, "files": {}}

    def _save_file_map(self, file_map: dict[str, Any]) -> None:
        """Persist file_map.json."""
        self._index_dir.mkdir(parents=True, exist_ok=True)
        (self._index_dir / self._FILE_MAP).write_text(
            json.dumps(file_map, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # --------------------------------------------------------------------------
    # Scan memory files
    # --------------------------------------------------------------------------

    def _scan_memory_files(self) -> dict[str, int]:
        """Scan memory dir for .md files. Returns {rel_path: mtime_ns}."""
        result: dict[str, int] = {}
        for p in self._memory_dir.rglob("*.md"):
            if p.name in ("index.md", "MEMORY.md"):
                continue
            # Exclude the index directory itself
            index_dir_name = self._index_dir.name
            if index_dir_name in p.parts:
                continue
            rel = p.relative_to(self._memory_dir).as_posix()
            result[rel] = p.stat().st_mtime_ns
        return result

    # --------------------------------------------------------------------------
    # Build — full rebuild
    # --------------------------------------------------------------------------

    def build_from_files(self, file_texts: dict[str, str]) -> None:
        """Build index from categorized memory files (full rebuild).

        *file_texts* maps relative source paths (e.g. ``conversations/index.md``)
        to their full text content.

        Uses IndexIDMap so incremental updates can add/remove by ID.
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

        try:
            import faiss
            import numpy as np
            # Disable MPS memory watermark so encode() can allocate tensors
            # on Apple Silicon even when the pool is near its artificial 9 GiB limit.
            import os as _os
            _os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")
        except ImportError:
            logger.warning("FAISS/numpy not installed, cannot build vector index")
            return

        texts = [c["text"] for c in chunks]
        embeddings = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

        dim = embeddings.shape[1]
        hnsw = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_INNER_PRODUCT)
        hnsw.hnsw.efConstruction = 80
        self._index = faiss.IndexIDMap(hnsw)

        ids = np.arange(len(chunks), dtype=np.int64)
        self._index.add_with_ids(np.array(embeddings, dtype=np.float32), ids)

        self._chunks = chunks  # list indexed by chunk_id, no gaps

        # Build file_map for future incremental builds
        file_map: dict[str, Any] = {"next_id": len(chunks), "files": {}}
        for source in file_texts:
            ids_for_source = [i for i, c in enumerate(chunks) if c["source"] == source]
            if ids_for_source:
                mtime_ns = 0
                try:
                    mtime_ns = (self._memory_dir / source).stat().st_mtime_ns
                except OSError:
                    pass
                file_map["files"][source] = {
                    "mtime_ns": mtime_ns,
                    "chunk_ids": ids_for_source,
                }
        self._save_file_map(file_map)
        self.save()

        logger.info("Built FAISS index with {} chunks from {} source files", len(chunks), len(file_texts))

    # --------------------------------------------------------------------------
    # Build — incremental (only re-embeds changed files)
    # --------------------------------------------------------------------------

    def build_incremental(self) -> bool:
        """Incremental index update. Only re-embeds changed files.

        Uses file_map.json to track (mtime_ns, chunk_ids) per source file.
        Detects new, changed, and deleted files; only re-embeds when necessary.

        Returns True if the index was updated.
        """
        file_map = self._load_file_map()
        current_files = self._scan_memory_files()

        old_files = set(file_map.get("files", {}))
        new_file_set = set(current_files)
        deleted = old_files - new_file_set
        common = old_files & new_file_set

        changed: set[str] = set()
        for rel in common:
            if file_map["files"][rel].get("mtime_ns") != current_files[rel]:
                changed.add(rel)

        new_files = new_file_set - old_files

        to_process = deleted | changed | new_files
        if not to_process:
            return False

        # --- First-time: no existing index, do full build ---
        if self._index is None and not self.load():
            file_texts: dict[str, str] = {}
            for rel in current_files:
                try:
                    content = (self._memory_dir / rel).read_text(encoding="utf-8")
                    if content.strip():
                        file_texts[rel] = content
                except Exception:
                    logger.warning("Failed to read memory file {}", rel, exc_info=True)
                    continue
            if file_texts:
                self.build_from_files(file_texts)
            return True

        if not self._load_model():
            return False

        try:
            import faiss
            import numpy as np
        except ImportError:
            logger.warning("FAISS not installed, cannot build vector index incrementally")
            return False

        try:
            return self._incremental_update(file_map, current_files, deleted, changed, new_files)
        except Exception:
            logger.warning("Incremental update failed, falling back to full rebuild")
            return self._fallback_full_rebuild(current_files)

    def _fallback_full_rebuild(self, current_files: dict[str, int]) -> bool:
        """Re-read all memory files and rebuild the FAISS index from scratch."""
        file_texts: dict[str, str] = {}
        for rel in current_files:
            try:
                content = (self._memory_dir / rel).read_text(encoding="utf-8")
                if content.strip():
                    file_texts[rel] = content
            except Exception:
                logger.warning("Failed to re-read memory file {}", rel, exc_info=True)
                continue
        if file_texts:
            self.build_from_files(file_texts)
        return True

    def _incremental_update(
        self,
        file_map: dict,
        current_files: dict[str, int],
        deleted: set[str],
        changed: set[str],
        new_files: set[str],
    ) -> bool:
        """Incremental FAISS update with remove_ids. Extracted so fallback is clean."""
        import faiss
        import numpy as np

        files = file_map.get("files", {})
        next_id = file_map.get("next_id", 0)

        # 1. Delete removed files
        for rel in deleted:
            entry = files.pop(rel, None)
            if entry and entry.get("chunk_ids"):
                ids_arr = np.array(entry["chunk_ids"], dtype=np.int64)
                self._index.remove_ids(faiss.IDSelectorArray(ids_arr))
                for cid in entry["chunk_ids"]:
                    if cid < len(self._chunks):
                        self._chunks[cid] = None

        # 2. Re-index changed files (remove old, add new)
        for rel in changed:
            try:
                content = (self._memory_dir / rel).read_text(encoding="utf-8")
            except Exception:
                logger.warning("Failed to read changed memory file {}", rel, exc_info=True)
                continue

            entry = files.get(rel)
            if entry and entry.get("chunk_ids"):
                ids_arr = np.array(entry["chunk_ids"], dtype=np.int64)
                self._index.remove_ids(faiss.IDSelectorArray(ids_arr))
                for cid in entry["chunk_ids"]:
                    if cid < len(self._chunks):
                        self._chunks[cid] = None

            if not content.strip():
                files.pop(rel, None)
                continue
            new_chunks = self._chunk_markdown(content, rel)
            if not new_chunks:
                continue

            texts = [c["text"] for c in new_chunks]
            embs = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
            new_ids = np.arange(next_id, next_id + len(new_chunks), dtype=np.int64)
            self._index.add_with_ids(np.array(embs, dtype=np.float32), new_ids)
            files[rel] = {"mtime_ns": current_files[rel], "chunk_ids": new_ids.tolist()}
            self._extend_chunks(next_id, new_chunks)
            next_id += len(new_chunks)

        # 3. Index new files
        for rel in new_files:
            try:
                content = (self._memory_dir / rel).read_text(encoding="utf-8")
            except Exception:
                logger.warning("Failed to read new memory file {}", rel, exc_info=True)
                continue
            if not content.strip():
                continue
            new_chunks = self._chunk_markdown(content, rel)
            if not new_chunks:
                continue

            texts = [c["text"] for c in new_chunks]
            embs = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
            new_ids = np.arange(next_id, next_id + len(new_chunks), dtype=np.int64)
            self._index.add_with_ids(np.array(embs, dtype=np.float32), new_ids)
            files[rel] = {"mtime_ns": current_files[rel], "chunk_ids": new_ids.tolist()}
            self._extend_chunks(next_id, new_chunks)
            next_id += len(new_chunks)

        file_map["next_id"] = next_id
        self._save_file_map(file_map)
        self.save()

        alive = sum(1 for c in self._chunks if c is not None)
        logger.info(
            "Incremental FAISS: {} del, {} chg, {} new ({} alive, next_id: {})",
            len(deleted), len(changed), len(new_files), alive, next_id,
        )
        return True

    def _extend_chunks(self, start_id: int, chunks: list[dict[str, Any]]) -> None:
        """Append *chunks* to ``self._chunks`` at the given *start_id*."""
        needed = start_id + len(chunks)
        if needed > len(self._chunks):
            self._chunks.extend([None] * (needed - len(self._chunks)))
        for i, c in enumerate(chunks):
            self._chunks[start_id + i] = c

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
            if chunk is None:
                continue
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
            if chunk is None:
                continue
            text = chunk.get("text", "").lower()
            count = sum(1 for t in terms if t in text)
            if count > 0:
                scored.append((idx, count))

        scored.sort(key=lambda x: -x[1])
        results = []
        for rank, (idx, _score) in enumerate(scored[:k]):
            chunk = self._chunks[idx]
            if chunk is None:
                continue
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
          1. If FAISS index available -> semantic similarity search with RRF fusion
          2. Otherwise -> pure keyword search (fallback)

        Returns up to *k* results with ``source``, ``heading``, ``text``, and ``score`` keys.
        """
        if not self._chunks:
            return []

        model_loaded = self._load_model()

        if model_loaded and self._index is not None and self._index.ntotal > 0:
            return self._faiss_search(query, k, min_score)

        logger.debug("No FAISS index - falling back to keyword search")
        return self._keyword_search(query, k)

    def _faiss_search(self, query: str, k: int, min_score: float) -> list[dict[str, Any]]:
        """Run FAISS + keyword hybrid search with RRF fusion."""
        import numpy as np

        query_vec = self._model.encode([query], normalize_embeddings=True)

        # Configure approximate search params if the index type supports them
        import faiss
        inner = faiss.downcast_index(self._index.index)
        if hasattr(inner, "hnsw"):
            inner.hnsw.ef_search = 40

        faiss_k = min(k * 3, self._index.ntotal)
        scores, indices = self._index.search(
            np.array(query_vec, dtype=np.float32), faiss_k,
        )

        faiss_results: list[dict[str, Any]] = []
        for v_rank, (score, idx) in enumerate(zip(scores[0], indices[0])):
            if idx < 0 or idx >= len(self._chunks):
                continue
            chunk = self._chunks[idx]
            if chunk is None:
                continue
            if score < min_score:
                continue
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
            if chunk is None:
                continue
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
            import shutil

            path = str(self._index_dir / self._INDEX_FILE)
            faiss.write_index(self._index, path)
            shutil.copy2(path, str(self._index_dir / self._INDEX_BAK))

        (self._index_dir / self._CHUNKS_FILE).write_text(
            json.dumps(self._chunks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self) -> bool:
        """Load persisted FAISS index and chunks from disk. Returns True on success.

        Handles migration from legacy non-IDMap format to IndexIDMap.
        """
        chunks_path = self._index_dir / self._CHUNKS_FILE
        if not chunks_path.exists():
            return False

        try:
            loaded_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load memory index chunks")
            return False

        # Ensure chunks is a list (not dict - legacy format was list)
        if isinstance(loaded_chunks, list):
            self._chunks = loaded_chunks
        else:
            logger.warning("Unknown chunks format, resetting")
            self._chunks = []

        # Load FAISS index (try primary, fall back to backup)
        try:
            import faiss
            import numpy as np
        except ImportError:
            return bool(self._chunks)

        index_path = self._index_dir / self._INDEX_FILE
        bak_path = self._index_dir / self._INDEX_BAK

        loaded_index = None
        if index_path.exists():
            try:
                loaded_index = faiss.read_index(str(index_path))
            except Exception:
                logger.warning("Failed to load FAISS index at {}, trying backup", index_path)
                if bak_path.exists():
                    try:
                        loaded_index = faiss.read_index(str(bak_path))
                        logger.info("Loaded FAISS index from backup")
                    except Exception:
                        logger.warning("Backup also corrupt")
        elif bak_path.exists():
            loaded_index = faiss.read_index(str(bak_path))
            logger.info("FAISS index missing, loaded from backup")

        if loaded_index is not None:
            if not isinstance(loaded_index, faiss.IndexIDMap):
                # Migration: wrap legacy index in IndexIDMap
                ntotal = loaded_index.ntotal
                if ntotal > 0 and self._chunks:
                    d = loaded_index.d
                    embs = np.zeros((ntotal, d), dtype=np.float32)
                    for i in range(ntotal):
                        loaded_index.reconstruct(i, embs[i])
                    ids = np.arange(ntotal, dtype=np.int64)
                    hnsw = faiss.IndexHNSWFlat(d, 32, faiss.METRIC_INNER_PRODUCT)
                    hnsw.hnsw.efConstruction = 80
                    idmap = faiss.IndexIDMap(hnsw)
                    idmap.add_with_ids(embs, ids)
                    self._index = idmap
                    self.save()
                    logger.info("Migrated legacy FAISS index to IndexIDMap ({} vectors)", ntotal)

                    # Build file_map from chunks if not present
                    if not (self._index_dir / self._FILE_MAP).exists():
                        file_map = self._build_file_map_from_chunks()
                        self._save_file_map(file_map)
                else:
                    self._index = loaded_index
            else:
                self._index = loaded_index

        return bool(self._chunks)

    def _build_file_map_from_chunks(self) -> dict[str, Any]:
        """Build file_map.json from existing chunks (migration helper)."""
        files: dict[str, Any] = {}
        for cid, chunk in enumerate(self._chunks):
            if chunk is None:
                continue
            source = chunk.get("source", "")
            if source not in files:
                files[source] = {"mtime_ns": 0, "chunk_ids": []}
            files[source]["chunk_ids"].append(cid)

        for source in files:
            p = self._memory_dir / source
            try:
                files[source]["mtime_ns"] = p.stat().st_mtime_ns
            except OSError:
                pass

        return {"next_id": len(self._chunks), "files": files}
