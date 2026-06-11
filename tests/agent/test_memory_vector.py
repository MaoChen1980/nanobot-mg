"""Tests for MemoryVectorIndex — FAISS-based vector index for memory retrieval.

Covers :mod:`nanobot.agent.memory_vector` — pure static methods, keyword search,
file map operations, and FAISS-dependent paths (mocked).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from nanobot.agent.memory_vector import MemoryVectorIndex


# ===========================================================================
# _chunk_markdown — pure static
# ===========================================================================

class TestChunkMarkdown:
    """``MemoryVectorIndex._chunk_markdown`` — split markdown by headings."""

    def test_empty_content(self):
        assert MemoryVectorIndex._chunk_markdown("", "test.md") == []

    def test_no_headings(self):
        result = MemoryVectorIndex._chunk_markdown("plain text\nwithout headings", "test.md")
        assert len(result) == 1
        assert result[0]["text"] == "plain text\nwithout headings"
        assert result[0]["source"] == "test.md"
        assert result[0]["heading"] == ""

    def test_splits_at_headings(self):
        """Heading line is included in text; heading label lags by one heading."""
        content = "## Title 1\ncontent one\n## Title 2\ncontent two"
        result = MemoryVectorIndex._chunk_markdown(content, "doc.md")
        assert len(result) == 2
        assert result[0]["text"] == "## Title 1\ncontent one"
        assert result[0]["heading"] == ""  # first heading not captured as label
        assert result[1]["text"] == "## Title 2\ncontent two"
        assert result[1]["heading"] == "Title 2"

    def test_oversized_chunk_split(self):
        content = "## H\n" + "a" * 600 + "\n\n" + "b" * 600
        result = MemoryVectorIndex._chunk_markdown(content, "large.md")
        assert len(result) == 2
        assert "(part 1)" in result[0]["heading"]
        assert "(part 2)" in result[1]["heading"]

    def test_source_is_preserved(self):
        content = "## Section\nbody"
        result = MemoryVectorIndex._chunk_markdown(content, "my_file.md")
        assert all(c["source"] == "my_file.md" for c in result)

    def test_consecutive_headings(self):
        """Each heading starts a new chunk; heading label lags by one heading."""
        content = "## H1\n## H2\nbody"
        result = MemoryVectorIndex._chunk_markdown(content, "test.md")
        assert len(result) == 2
        assert result[0] == {"text": "## H1", "source": "test.md", "heading": ""}
        assert result[1] == {"text": "## H2\nbody", "source": "test.md", "heading": "H2"}

    def test_heading_without_following_content(self):
        content = "lead\n## H1"
        result = MemoryVectorIndex._chunk_markdown(content, "test.md")
        assert len(result) == 2
        assert result[0]["heading"] == ""
        assert result[0]["text"] == "lead"
        assert result[1]["heading"] == "H1"
        assert result[1]["text"] == "## H1"

    def test_non_h2_headings(self):
        """Only ## (not #) triggers split."""
        content = "# Title\nbody text"
        result = MemoryVectorIndex._chunk_markdown(content, "test.md")
        assert len(result) == 1


# ===========================================================================
# _split_text — pure static
# ===========================================================================

class TestSplitText:
    """``MemoryVectorIndex._split_text`` — split at paragraph/line boundaries."""

    def test_short_text(self):
        assert MemoryVectorIndex._split_text("short", 100) == ["short"]

    def test_splits_at_paragraph_boundary(self):
        text = "A" * 60 + "\n\n" + "B" * 60
        result = MemoryVectorIndex._split_text(text, 80)
        assert len(result) == 2

    def test_splits_at_line_boundary(self):
        text = "A" * 60 + "\n" + "B" * 60
        result = MemoryVectorIndex._split_text(text, 80)
        assert len(result) == 2

    def test_splits_at_absolute_max_when_no_boundary(self):
        text = "A" * 200
        result = MemoryVectorIndex._split_text(text, 50)
        assert all(len(p) <= 50 for p in result)
        assert len(result) >= 4

    def test_empty_text(self):
        assert MemoryVectorIndex._split_text("", 100) == []


# ===========================================================================
# _extract_terms — pure static
# ===========================================================================

class TestExtractTerms:
    """``MemoryVectorIndex._extract_terms`` — extract meaningful terms."""

    def test_english_words(self):
        terms = MemoryVectorIndex._extract_terms("hello world test")
        assert "hello" in terms
        assert "world" in terms
        assert "test" in terms

    def test_excludes_single_letter(self):
        terms = MemoryVectorIndex._extract_terms("a bee")
        assert "bee" in terms
        assert "a" not in terms

    def test_cjk_bigrams(self):
        terms = MemoryVectorIndex._extract_terms("机器学习")
        assert "机器" in terms
        assert "器学" in terms
        assert "学习" in terms

    def test_mixed_english_and_cjk(self):
        terms = MemoryVectorIndex._extract_terms("Python 机器学习")
        assert "python" in terms
        assert "机器" in terms
        assert "器学" in terms
        assert "学习" in terms

    def test_empty_query(self):
        assert MemoryVectorIndex._extract_terms("") == set()


# ===========================================================================
# _keyword_search — uses self._chunks
# ===========================================================================

class TestKeywordSearch:
    """``MemoryVectorIndex._keyword_search`` — keyword-based search."""

    def test_empty_chunks(self):
        idx = MemoryVectorIndex(Path("/tmp/mem"))
        assert idx._keyword_search("test", k=5) == []

    def test_returns_matching_chunks(self):
        idx = MemoryVectorIndex(Path("/tmp/mem"))
        idx._chunks = [
            {"text": "hello world", "source": "a.md", "heading": "H1"},
            {"text": "foo bar", "source": "b.md", "heading": "H2"},
            {"text": "hello machine learning", "source": "c.md", "heading": "H3"},
        ]
        results = idx._keyword_search("hello", k=5)
        assert len(results) == 2
        assert any(r["source"] == "a.md" for r in results)
        assert any(r["source"] == "c.md" for r in results)

    def test_respects_k_limit(self):
        idx = MemoryVectorIndex(Path("/tmp/mem"))
        idx._chunks = [
            {"text": "hello a", "source": "a.md", "heading": "H1"},
            {"text": "hello b", "source": "b.md", "heading": "H2"},
            {"text": "hello c", "source": "c.md", "heading": "H3"},
        ]
        results = idx._keyword_search("hello", k=2)
        assert len(results) == 2

    def test_skips_deleted_chunks(self):
        idx = MemoryVectorIndex(Path("/tmp/mem"))
        idx._chunks = [
            {"text": "hello world", "source": "a.md", "heading": "H1"},
            None,
            {"text": "hello again", "source": "b.md", "heading": "H2"},
        ]
        results = idx._keyword_search("hello", k=5)
        assert len(results) == 2

    def test_no_match(self):
        idx = MemoryVectorIndex(Path("/tmp/mem"))
        idx._chunks = [{"text": "foo bar", "source": "a.md", "heading": "H1"}]
        assert idx._keyword_search("xyz", k=5) == []


# ===========================================================================
# _scan_memory_files — file system
# ===========================================================================

class TestScanMemoryFiles:
    """``MemoryVectorIndex._scan_memory_files`` — enumerate .md files."""

    def test_returns_mtime_map(self, tmp_path):
        (tmp_path / "memories").mkdir()
        (tmp_path / "memories" / "test.md").write_text("hello", encoding="utf-8")
        (tmp_path / "memories" / "sub").mkdir()
        (tmp_path / "memories" / "sub" / "nested.md").write_text("world", encoding="utf-8")

        idx = MemoryVectorIndex(tmp_path / "memories")
        result = idx._scan_memory_files()

        assert "test.md" in result
        assert "sub/nested.md" in result
        assert isinstance(result["test.md"], int)
        assert result["test.md"] > 0

    def test_excludes_index_and_memory_md(self, tmp_path):
        (tmp_path / "memories").mkdir()
        for name in ("index.md", "MEMORY.md", "normal.md"):
            (tmp_path / "memories" / name).write_text("x", encoding="utf-8")

        idx = MemoryVectorIndex(tmp_path / "memories")
        result = idx._scan_memory_files()

        assert "normal.md" in result
        assert "index.md" not in result
        assert "MEMORY.md" not in result

    def test_excludes_vector_index_dir(self, tmp_path):
        (tmp_path / "memories").mkdir()
        (tmp_path / "memories" / "note.md").write_text("x", encoding="utf-8")
        (tmp_path / "memories" / ".vector_index").mkdir()
        (tmp_path / "memories" / ".vector_index" / "meta.md").write_text("x", encoding="utf-8")

        idx = MemoryVectorIndex(tmp_path / "memories")
        result = idx._scan_memory_files()

        assert "note.md" in result
        assert ".vector_index/meta.md" not in result

    def test_empty_directory(self, tmp_path):
        (tmp_path / "memories").mkdir()
        idx = MemoryVectorIndex(tmp_path / "memories")
        assert idx._scan_memory_files() == {}


# ===========================================================================
# File map (load / save)
# ===========================================================================

class TestFileMap:
    """``_load_file_map`` and ``_save_file_map``."""

    def test_load_missing_returns_default(self, tmp_path):
        idx = MemoryVectorIndex(tmp_path / "mem")
        result = idx._load_file_map()
        assert result == {"next_id": 0, "files": {}}

    def test_save_and_load(self, tmp_path):
        idx = MemoryVectorIndex(tmp_path / "mem")
        data = {"next_id": 5, "files": {"a.md": {"mtime_ns": 100, "chunk_ids": [0, 1]}}}
        idx._save_file_map(data)

        loaded = idx._load_file_map()
        assert loaded == data

    def test_load_corrupted_returns_default(self, tmp_path):
        idx = MemoryVectorIndex(tmp_path / "mem")
        idx._index_dir.mkdir(parents=True, exist_ok=True)
        (idx._index_dir / "file_map.json").write_text("corrupt", encoding="utf-8")

        result = idx._load_file_map()
        assert result == {"next_id": 0, "files": {}}

    def test_save_creates_directory(self, tmp_path):
        idx = MemoryVectorIndex(tmp_path / "deeply" / "nested" / "mem")
        idx._save_file_map({"next_id": 0, "files": {}})
        assert (idx._index_dir / "file_map.json").exists()


# ===========================================================================
# search — dispatch logic
# ===========================================================================

class TestSearch:
    """``search`` — dispatches to keyword or hybrid search."""

    def test_empty_chunks_returns_empty(self, tmp_path):
        idx = MemoryVectorIndex(tmp_path)
        assert idx.search("anything") == []

    def test_keyword_fallback_when_no_index(self, tmp_path):
        idx = MemoryVectorIndex(tmp_path)
        idx._chunks = [{"text": "hello world", "source": "a.md", "heading": "H1"}]
        results = idx.search("hello")
        assert len(results) >= 1
        assert results[0]["source"] == "a.md"


# ===========================================================================
# build_from_files
# ===========================================================================

class TestBuildFromFiles:
    """``build_from_files`` — full rebuild with mocked FAISS/sentence-transformers."""

    @patch("nanobot.agent.memory_vector.MemoryVectorIndex._load_model", return_value=True)
    def test_empty_files(self, mock_load, tmp_path):
        idx = MemoryVectorIndex(tmp_path)
        idx.build_from_files({})
        assert idx._chunks == []

    @patch("nanobot.agent.memory_vector.MemoryVectorIndex._load_model", return_value=True)
    def test_skips_blank_content(self, mock_load, tmp_path):
        idx = MemoryVectorIndex(tmp_path)
        idx.build_from_files({"a.md": "  "})
        assert idx._chunks == []

    @patch("nanobot.agent.memory_vector.MemoryVectorIndex._load_model", return_value=True)
    def test_empty_content_skipped(self, mock_load, tmp_path):
        idx = MemoryVectorIndex(tmp_path)
        idx.build_from_files({"a.md": ""})
        assert idx._chunks == []

    @patch("nanobot.agent.memory_vector.MemoryVectorIndex._load_model", return_value=False)
    def test_model_not_available(self, mock_load, tmp_path):
        idx = MemoryVectorIndex(tmp_path)
        idx.build_from_files({"a.md": "some content"})
        assert idx._chunks == []

    def test_with_mocked_faiss_and_model(self, tmp_path):
        import sys

        idx = MemoryVectorIndex(tmp_path)

        faiss_mock = MagicMock()
        np_mock = MagicMock()

        # Set up FAISS mock: IndexIDMap, IndexHNSWFlat, METRIC_INNER_PRODUCT
        idmap = MagicMock()
        hnsw = MagicMock()
        faiss_mock.IndexIDMap.return_value = idmap
        faiss_mock.IndexHNSWFlat.return_value = hnsw
        faiss_mock.METRIC_INNER_PRODUCT = 0

        # Mock numpy: arange for IDs, array conversion, and embeddings.shape
        np_mock.arange.return_value = [0, 1]

        embeddings_mock = MagicMock()
        embeddings_mock.shape = (2, 2)
        np_mock.array.return_value = embeddings_mock

        with (
            patch("nanobot.agent.memory_vector.MemoryVectorIndex._load_model", return_value=True),
            patch.dict("sys.modules", {"faiss": faiss_mock, "numpy": np_mock}),
            patch.object(idx, "_model") as mock_model,
            patch.object(idx, "save"),
        ):
            mock_model.encode.return_value = embeddings_mock
            idx.build_from_files({"a.md": "hello", "b.md": "## Section\nbody"})

        assert len(idx._chunks) == 2


# ===========================================================================
# _extend_chunks — helper for incremental updates
# ===========================================================================

class TestExtendChunks:
    """``_extend_chunks`` — append chunks at given start_id."""

    def test_appends_empty(self):
        idx = MemoryVectorIndex(Path("/tmp/mem"))
        idx._chunks = [{"text": "existing"}]
        idx._extend_chunks(1, [])
        assert len(idx._chunks) == 1

    def test_appends_at_start_id(self):
        idx = MemoryVectorIndex(Path("/tmp/mem"))
        idx._chunks = []
        new_chunks = [{"text": "a"}, {"text": "b"}]
        idx._extend_chunks(0, new_chunks)
        assert len(idx._chunks) == 2
        assert idx._chunks[0]["text"] == "a"
        assert idx._chunks[1]["text"] == "b"

    def test_pads_with_none_when_gap(self):
        idx = MemoryVectorIndex(Path("/tmp/mem"))
        idx._chunks = [{"text": "first"}]
        new_chunks = [{"text": "third"}]
        idx._extend_chunks(2, new_chunks)
        assert len(idx._chunks) == 3
        assert idx._chunks[0]["text"] == "first"
        assert idx._chunks[1] is None
        assert idx._chunks[2]["text"] == "third"


# ===========================================================================
# _load_model — lazy initialization
# ===========================================================================

class TestLoadModel:
    """``_load_model`` — lazy-load SentenceTransformer."""

    def test_returns_true_when_already_loaded(self, tmp_path):
        """When self._model is set, _load_model returns True immediately."""
        idx = MemoryVectorIndex(tmp_path)
        idx._model = MagicMock()
        assert idx._load_model() is True

    def test_returns_false_on_import_error(self, tmp_path):
        """When SentenceTransformer import fails, returns False with warning."""
        with patch("sentence_transformers.SentenceTransformer",
                   side_effect=ImportError("not installed")):
            idx = MemoryVectorIndex(tmp_path)
            result = idx._load_model()
        assert result is False

    def test_returns_false_on_init_error(self, tmp_path):
        """When SentenceTransformer constructor fails, returns False with warning."""
        with patch("sentence_transformers.SentenceTransformer",
                   side_effect=OSError("model file not found")):
            idx = MemoryVectorIndex(tmp_path)
            result = idx._load_model()
        assert result is False

    def test_failure_not_cached(self, tmp_path):
        """A failed _load_model can be retried (model stays None)."""
        idx = MemoryVectorIndex(tmp_path)
        with patch("sentence_transformers.SentenceTransformer",
                   side_effect=ImportError("not installed")):
            assert idx._load_model() is False
        assert idx._model is None
