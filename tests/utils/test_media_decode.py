"""Tests for ``nanobot.utils.media_decode``."""

from __future__ import annotations

import base64

import pytest

from nanobot.utils.media_decode import (
    DEFAULT_MAX_BYTES,
    FileSizeExceeded,
    MAX_FILE_SIZE,
    save_base64_data_url,
)


def _data_url(payload: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


def test_saves_png_with_correct_extension(tmp_path) -> None:
    result = save_base64_data_url(_data_url(b"fake png"), tmp_path)
    assert result is not None
    assert result.endswith(".png")
    assert (tmp_path / result.split("/")[-1]).read_bytes() == b"fake png"


def test_returns_none_for_malformed_data_url(tmp_path) -> None:
    assert save_base64_data_url("not-a-data-url", tmp_path) is None


def test_returns_none_for_broken_base64(tmp_path) -> None:
    # Python's b64decode strips non-alphabet chars by default, so we need a
    # payload whose alphabet-filtered length breaks padding.
    assert save_base64_data_url("data:image/png;base64,not-valid-base64!!!", tmp_path) is None


def test_unknown_mime_falls_back_to_bin(tmp_path) -> None:
    result = save_base64_data_url(_data_url(b"xyz", mime="unknown/type"), tmp_path)
    assert result is not None
    assert result.endswith(".bin")


def test_default_limit_is_10mb(tmp_path) -> None:
    """Backwards-compatible default — the API path depends on this."""
    assert DEFAULT_MAX_BYTES == 10 * 1024 * 1024
    assert MAX_FILE_SIZE == 10 * 1024 * 1024

    oversized = b"x" * (11 * 1024 * 1024)
    with pytest.raises(FileSizeExceeded, match="10MB limit"):
        save_base64_data_url(_data_url(oversized), tmp_path)


def test_explicit_max_bytes_overrides_default(tmp_path) -> None:
    """WS channel passes 8 MB; a 9 MB payload should be rejected there even
    though it would pass the 10 MB API limit."""
    payload = b"y" * (9 * 1024 * 1024)
    with pytest.raises(FileSizeExceeded, match="8MB limit"):
        save_base64_data_url(_data_url(payload), tmp_path, max_bytes=8 * 1024 * 1024)


def test_saved_file_lives_under_media_dir(tmp_path) -> None:
    result = save_base64_data_url(_data_url(b"ok"), tmp_path)
    assert result is not None
    assert result.startswith(str(tmp_path))


def test_legacy_symbols_reexported_from_api_server() -> None:
    """Existing tests import ``_save_base64_data_url`` / ``_FileSizeExceeded``
    from ``nanobot.api.server`` — keep the aliases working."""
    from nanobot.api import server

    assert server._save_base64_data_url is save_base64_data_url
    assert server._FileSizeExceeded is FileSizeExceeded
    assert server.MAX_FILE_SIZE == MAX_FILE_SIZE


# ---------------------------------------------------------------------------
# detect_image_mime
# ---------------------------------------------------------------------------


def test_detect_image_mime_jpeg():
    from nanobot.utils.media_decode import detect_image_mime
    assert detect_image_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"


def test_detect_image_mime_gif87():
    from nanobot.utils.media_decode import detect_image_mime
    assert detect_image_mime(b"GIF87a\x00\x00") == "image/gif"


def test_detect_image_mime_gif89():
    from nanobot.utils.media_decode import detect_image_mime
    assert detect_image_mime(b"GIF89a\x00\x00") == "image/gif"


def test_detect_image_mime_webp():
    from nanobot.utils.media_decode import detect_image_mime
    assert detect_image_mime(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"


def test_detect_image_mime_unknown():
    from nanobot.utils.media_decode import detect_image_mime
    assert detect_image_mime(b"\x00\x01\x02\x03") is None


def test_detect_image_mime_empty():
    from nanobot.utils.media_decode import detect_image_mime
    assert detect_image_mime(b"") is None


# ---------------------------------------------------------------------------
# build_image_content_blocks
# ---------------------------------------------------------------------------


def test_build_image_content_blocks():
    from nanobot.utils.media_decode import build_image_content_blocks
    result = build_image_content_blocks(b"raw", "image/png", "/tmp/test.png", "test image")
    assert result[0]["type"] == "image_url"
    assert "data:image/png;base64," in result[0]["image_url"]["url"]
    assert result[0]["_meta"]["path"] == "/tmp/test.png"
    assert result[1] == {"type": "text", "text": "test image"}


# ---------------------------------------------------------------------------
# image_placeholder_text
# ---------------------------------------------------------------------------


def test_image_placeholder_text_with_path():
    from nanobot.utils.media_decode import image_placeholder_text
    assert image_placeholder_text("/tmp/test.png") == "[image: /tmp/test.png]"


def test_image_placeholder_text_none():
    from nanobot.utils.media_decode import image_placeholder_text
    assert image_placeholder_text(None) == "[image]"


def test_compress_image_corrupt_data_returns_raw():
    """Corrupt image data -> returns raw bytes unchanged."""
    from nanobot.utils.media_decode import compress_image

    result_bytes, result_mime = compress_image(b"not-a-real-image-file-data")
    assert result_bytes == b"not-a-real-image-file-data"
    assert result_mime == "image/png"


def test_compress_image_empty_bytes_returns_raw():
    """Empty bytes for compress_image -> returns raw."""
    from nanobot.utils.media_decode import compress_image

    result_bytes, result_mime = compress_image(b"")
    # Empty bytes won't open as image -> exception path
    assert result_bytes == b""

    from nanobot.utils.media_decode import image_placeholder_text
    assert image_placeholder_text(None, empty="[photo]") == "[photo]"
