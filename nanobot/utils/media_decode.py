"""Shared helpers for decoding ``data:...;base64,...`` URLs to disk.

Historically lived in ``nanobot.api.server``; now shared by the WebSocket
channel so the ``api`` + ``websocket`` ingress paths apply the same parsing,
size guard, and filesystem layout.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import re
import uuid
from pathlib import Path

from typing import Any

from loguru import logger

from nanobot.utils.helpers import safe_filename

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_FILE_SIZE = DEFAULT_MAX_BYTES

_DATA_URL_RE = re.compile(r"^data:([^;]+);base64,(.+)$", re.DOTALL)

# Max dimension (longest edge) for image compression — keeps detail good
# while drastically reducing base64 size and visual-token cost.
_MAX_IMAGE_DIMENSION = 2048

# Default target size for grid-based compression: under this threshold
# the image stays inline as base64 in tool results (used as fallback when
# no explicit max_bytes is passed).
_DEFAULT_TARGET_BYTES = 32 * 1024

# Quality levels tried during grid search, from best to worst.
_QUALITY_STEPS = (85, 75, 65, 55, 45, 35, 25)

# Side sizes tried during grid search (sorted, deduped at call time).
_SIDE_CANDIDATES = (2048, 1800, 1600, 1400, 1200, 1000, 800, 600)


class FileSizeExceeded(Exception):
    """Raised when a decoded payload exceeds the caller's size limit."""


def save_base64_data_url(
    data_url: str,
    media_dir: Path,
    *,
    max_bytes: int | None = None,
) -> str | None:
    """Decode a ``data:<mime>;base64,<payload>`` URL and persist it.

    Returns the absolute path on success, ``None`` when the URL shape or the
    base64 payload itself is malformed. Raises :class:`FileSizeExceeded`
    when the decoded payload is larger than ``max_bytes`` (default 10 MB).
    """
    m = _DATA_URL_RE.match(data_url)
    if not m:
        return None
    mime_type, b64_payload = m.group(1), m.group(2)
    try:
        raw = base64.b64decode(b64_payload)
    except Exception:
        logger.debug("Failed to decode base64 data URL")
        return None
    limit = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    if len(raw) > limit:
        raise FileSizeExceeded(f"File exceeds {limit // (1024 * 1024)}MB limit")
    ext = mimetypes.guess_extension(mime_type) or ".bin"
    filename = f"{uuid.uuid4().hex[:12]}{ext}"
    dest = media_dir / safe_filename(filename)
    dest.write_bytes(raw)
    return str(dest)


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes, ignoring file extension."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _build_side_grid(current_max: int, absolute_max: int) -> list[int]:
    """Build descending list of side-size candidates capped by *absolute_max*.

    Mirrors openclaw's ``buildImageResizeSideGrid``: starts from the smaller
    of ``current_max`` / ``absolute_max``, then includes predefined steps,
    deduplicates, and sorts descending.
    """
    start = min(current_max, absolute_max)
    seen: set[int] = set()
    result: list[int] = []
    for v in (start, *_SIDE_CANDIDATES):
        capped = min(v, absolute_max)
        if capped > 0 and capped not in seen:
            seen.add(capped)
            result.append(capped)
    result.sort(reverse=True)
    return result


def compress_image(
    raw: bytes,
    mime: str | None = None,
    *,
    max_bytes: int | None = None,
    max_dimension: int = _MAX_IMAGE_DIMENSION,
) -> tuple[bytes, str]:
    """Downscale and re-encode *raw* image bytes to reduce size.

    Grid-based compression (inspired by openclaw's ``resizeImageBase64IfNeeded``):
    tries multiple side-size × quality combinations and returns the first result
    that fits under ``max_bytes``.  Tracks the smallest encoding as a fallback.

    When ``max_bytes`` is ``None``, the function still applies grid compression
    but with a generous default target (``_DEFAULT_TARGET_BYTES`` ≈ 32 KB).

    Returns ``(compressed_bytes, output_mime)`` — always JPEG for photographic
    images since it compresses far better than PNG at the same perceptual quality.
    GIFs are preserved as-is (they are small by nature).
    """
    try:
        from PIL import Image
    except ImportError:
        return raw, mime or "image/png"

    if mime == "image/gif":
        return raw, mime

    try:
        img = Image.open(io.BytesIO(raw))
    except Exception:
        logger.warning("Failed to open image for decoding", exc_info=True)
        return raw, mime or "image/png"

    target = _DEFAULT_TARGET_BYTES if max_bytes is None else max_bytes

    # If raw is already small enough and within dimension limits, fast-path.
    if len(raw) <= target:
        w, h = img.size
        if max(w, h) <= max_dimension:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            return buf.getvalue(), "image/jpeg"

    original_max = max(img.size)
    side_grid = _build_side_grid(original_max, max_dimension)

    smallest: tuple[int, bytes] | None = None  # (size, data)

    for side in side_grid:
        w, h = img.size
        if max(w, h) > side:
            ratio = side / max(w, h)
            resized = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        else:
            resized = img.copy()

        if resized.mode in ("RGBA", "P"):
            resized = resized.convert("RGB")

        for quality in _QUALITY_STEPS:
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            size = len(data)

            if smallest is None or size < smallest[0]:
                smallest = (size, data)

            if size <= target:
                return data, "image/jpeg"

    # Fallback to the smallest encoding we found.
    if smallest is not None:
        return smallest[1], "image/jpeg"

    # Absolute fallback — should not normally be reached.
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=35, optimize=True)
    return buf.getvalue(), "image/jpeg"


def build_image_content_blocks(
    raw: bytes, mime: str, path: str, label: str,
    *,
    max_bytes: int | None = None,
) -> list[dict[str, Any]]:
    """Build native image blocks plus a short text label.

    When *max_bytes* is provided, images are compressed with grid search to
    fit under the budget (see ``compress_image``).  Otherwise a generous
    default target (32 KB) is used.
    """
    compressed, out_mime = compress_image(raw, mime, max_bytes=max_bytes)
    b64 = base64.b64encode(compressed).decode()
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{out_mime};base64,{b64}"},
            "_meta": {"path": path},
        },
        {"type": "text", "text": label},
    ]


def image_placeholder_text(path: str | None, *, empty: str = "[image]") -> str:
    """Build an image placeholder string."""
    return f"[image: {path}]" if path else empty


def strip_image_blocks(messages: list[dict]) -> None:
    """Replace image_url blocks with text placeholders in-place.

    Images should only be sent to the LLM once — after the model has seen
    them, replace the heavy base64 payload with a lightweight ``[image: path]``
    reference so subsequent turns don't re-send multiple megabytes of data.
    The model can always call ``read_file`` again if it needs to re-examine
    the image.
    """
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for i, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "image_url":
                path = (block.get("_meta") or {}).get("path", "")
                content[i] = {"type": "text", "text": image_placeholder_text(path)}
