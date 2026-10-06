"""Prepare bounded, cached image copies without changing source artifacts."""

import base64
import binascii
import hashlib
import io
import threading
import time
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


@dataclass
class PreparationStats:
    image_count: int = 0
    changed_count: int = 0
    cache_hits: int = 0
    original_bytes: int = 0
    prepared_bytes: int = 0
    elapsed_ms: float = 0

    def headers(self):
        return {
            "x-kt-image-count": str(self.image_count),
            "x-kt-image-prepared-count": str(self.changed_count),
            "x-kt-image-cache-hits": str(self.cache_hits),
            "x-kt-image-original-bytes": str(self.original_bytes),
            "x-kt-image-prepared-bytes": str(self.prepared_bytes),
            "x-kt-image-prep-ms": f"{self.elapsed_ms:.3f}",
        }


class ImagePreparer:
    """Cache router-compatible inline image representations for outbound requests."""

    def __init__(self, *, max_entries=256, max_cache_bytes=64 * 1024**2):
        self.max_entries = max_entries
        self.max_cache_bytes = max_cache_bytes
        self.entries = OrderedDict()
        self.cache_bytes = 0
        self._lock = threading.Lock()

    def prepare(self, body: dict[str, Any], *, max_image_bytes: int = 512_000):
        if max_image_bytes <= 0:
            raise ValueError("max_image_bytes must be positive")
        started = time.monotonic()
        stats = PreparationStats()
        result = dict(body)
        for key in ("messages", "input"):
            items = body.get(key)
            if not isinstance(items, list):
                continue
            result[key] = []
            for item in items:
                updated = item
                if isinstance(item, dict):
                    kind = item.get("type", "message")
                    content_key = (
                        "content" if key == "messages" or kind == "message" else None
                    )
                    if key == "input" and kind == "function_call_output":
                        content_key = "output"
                    if content_key and isinstance(item.get(content_key), list):
                        updated = {
                            **item,
                            content_key: self._content(
                                item[content_key], stats, max_image_bytes
                            ),
                        }
                result[key].append(updated)
        stats.elapsed_ms = (time.monotonic() - started) * 1000
        return result, stats

    def _content(self, content, stats, max_image_bytes):
        result = []
        for block in content:
            updated = block
            if isinstance(block, dict):
                image = block.get("image_url")
                source = block.get("source")
                if block.get("type") == "image_url" and isinstance(image, dict):
                    url = image.get("url")
                    if isinstance(url, str):
                        prepared = self._url(url, stats, max_image_bytes)
                        if prepared != url:
                            updated = {**block, "image_url": {**image, "url": prepared}}
                elif block.get("type") == "input_image" and isinstance(image, str):
                    prepared = self._url(image, stats, max_image_bytes)
                    if prepared != image:
                        updated = {**block, "image_url": prepared}
                elif (
                    block.get("type") == "image"
                    and isinstance(source, dict)
                    and source.get("type") == "base64"
                    and isinstance(source.get("data"), str)
                ):
                    url = f"data:{source.get('media_type', '')};base64,{source['data']}"
                    prepared = self._url(url, stats, max_image_bytes)
                    if prepared != url:
                        header, encoded = prepared.split(",", 1)
                        updated = {
                            **block,
                            "source": {
                                **source,
                                "media_type": header[5:].split(";")[0],
                                "data": encoded,
                            },
                        }
                elif block.get("type") == "tool_result" and isinstance(
                    block.get("content"), list
                ):
                    updated = {
                        **block,
                        "content": self._content(
                            block["content"], stats, max_image_bytes
                        ),
                    }
            result.append(updated)
        return result

    def _url(self, url, stats, max_image_bytes):
        header, separator, encoded = url.partition(",")
        if not separator or header.lower() not in {
            "data:image/png;base64",
            "data:image/jpeg;base64",
            "data:image/webp;base64",
            "data:image/gif;base64",
        }:
            return url
        stats.image_count += 1
        digest = (hashlib.sha256(url.encode()).digest(), max_image_bytes)
        with self._lock:
            cached = self.entries.get(digest)
            if cached is not None:
                self.entries.move_to_end(digest)
        if cached is not None:
            stats.cache_hits += 1
            prepared, original_size, prepared_size, _ = cached
        else:
            prepared, original_size, prepared_size = None, 0, 0
            try:
                original = base64.b64decode(encoded, validate=True)
                original_size = prepared_size = len(original)
                data, mime = self._encode(original, max_image_bytes)
                if data != original:
                    prepared = f"data:{mime};base64," + base64.b64encode(data).decode()
                    prepared_size = len(data)
            except (
                ValueError,
                OSError,
                binascii.Error,
                UnidentifiedImageError,
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
            ):
                pass
            cost = len(prepared) if prepared is not None else 64
            if cost <= self.max_cache_bytes:
                with self._lock:
                    old = self.entries.pop(digest, None)
                    if old is not None:
                        self.cache_bytes -= old[3]
                    self.entries[digest] = prepared, original_size, prepared_size, cost
                    self.cache_bytes += cost
                    while self.entries and (
                        len(self.entries) > self.max_entries
                        or self.cache_bytes > self.max_cache_bytes
                    ):
                        _, discarded = self.entries.popitem(last=False)
                        self.cache_bytes -= discarded[3]
        stats.original_bytes += original_size
        stats.prepared_bytes += prepared_size
        if prepared is not None:
            stats.changed_count += 1
        return prepared if prepared is not None else url

    @staticmethod
    def _encode(original, max_image_bytes=512_000):
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(original)) as source:
                # Camera MPO files are JPEGs whose first frame is the photo.
                is_mpo = source.format == "MPO"
                if (
                    (source.format not in {"PNG", "JPEG", "WEBP"} and not is_mpo)
                    or (getattr(source, "n_frames", 1) != 1 and not is_mpo)
                    or source.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "CMYK"}
                ):
                    return original, ""
                if source.width * source.height > 40_000_000:
                    return original, ""
                if len(original) <= max_image_bytes and max(source.size) <= 2000:
                    return original, ""
                preserve_png = (
                    source.format == "PNG" and len(original) <= max_image_bytes
                )
                image = ImageOps.exif_transpose(source)
                image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
                has_alpha = "A" in image.getbands() or "transparency" in image.info
                if has_alpha:
                    image = image.convert("RGBA")
                    if image.getchannel("A").getextrema() == (255, 255):
                        image = image.convert("RGB")
                        has_alpha = False
                else:
                    image = image.convert("RGB")
                if preserve_png or has_alpha or image.getcolors(256) is not None:
                    stream = io.BytesIO()
                    image.save(stream, "PNG", optimize=True)
                    png = stream.getvalue()
                    if has_alpha and len(png) >= len(original):
                        return original, ""
                    if has_alpha or len(png) <= max_image_bytes:
                        return png, "image/png"
                for quality in (90, 85, 80, 75, 70, 60, 50, 40, 30, 20):
                    stream = io.BytesIO()
                    image.save(
                        stream, "JPEG", quality=quality, optimize=True, subsampling=2
                    )
                    data = stream.getvalue()
                    if len(data) <= max_image_bytes:
                        return data, "image/jpeg"
                return data, "image/jpeg"
