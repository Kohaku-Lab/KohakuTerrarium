"""Safe normalization of arbitrary tool output before storage or context.

Text is byte-budgeted, multimodal content has a safe plain-text rendering, and
inline binary data is materialized to session artifacts or explicitly elided.
"""

import base64
import re
import time
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.core.constants import TOOL_OUTPUT_PREVIEW_CHARS
from kohakuterrarium.llm.message import (
    ContentPart,
    FilePart,
    ImagePart,
    TextPart,
    normalize_content_parts,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>image/(?P<ext>[\w.+-]+));base64,(?P<b64>.*)$",
    re.DOTALL,
)


@dataclass(slots=True)
class OutputStats:
    """Summarize the safe text rendering of normalized output."""

    text: str = ""
    lines: int = 0
    bytes: int = 0
    preview: str = ""


@dataclass(slots=True)
class NormalizedToolOutput:
    """Bundle normalized output with its text statistics and metadata."""

    output: str | list[ContentPart]
    stats: OutputStats
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.stats.text


def truncate_text_utf8(
    text: str,
    max_bytes: int,
    *,
    tool_name: str = "",
    saved_to: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Truncate tool text at a valid UTF-8 boundary.

    The byte limit applies to retained source text; the explanatory truncation note
    may make the final rendered value slightly larger.
    """
    raw = text.encode("utf-8")
    original_bytes = len(raw)
    if max_bytes <= 0 or original_bytes <= max_bytes:
        return text, {"truncated": False, "original_text_bytes": original_bytes}

    prefix = raw[:max_bytes].decode("utf-8", errors="ignore")
    kept_bytes = len(prefix.encode("utf-8"))
    omitted = max(0, original_bytes - kept_bytes)
    note = _truncation_note(max_bytes, omitted, tool_name=tool_name, saved_to=saved_to)
    return (
        prefix + note,
        {
            "truncated": True,
            "original_text_bytes": original_bytes,
            "max_output_bytes": max_bytes,
            "omitted_text_bytes": omitted,
        },
    )


def _truncation_note(
    max_bytes: int,
    omitted: int,
    *,
    tool_name: str = "",
    saved_to: str | None = None,
) -> str:
    """Build the truncation hint appropriate to the tool and its fallback path."""
    if saved_to:
        return (
            f"\n... [tool output truncated to {max_bytes} bytes; "
            f"{omitted} bytes omitted. Full output saved to {saved_to}; "
            f"use read to view it]"
        )
    if tool_name == "read":
        return (
            f"\n... [tool output truncated to {max_bytes} bytes; "
            f"{omitted} bytes omitted. Re-run the tool with a smaller scope "
            f"(e.g. offset/limit) to read the omitted sections]"
        )
    return (
        f"\n... [tool output truncated to {max_bytes} bytes; "
        f"{omitted} bytes omitted. Re-run the tool with a smaller scope]"
    )


def output_stats(
    content: str | list[ContentPart],
    *,
    preview_chars: int = TOOL_OUTPUT_PREVIEW_CHARS,
) -> OutputStats:
    """Return safe text rendering, line count, byte count, and preview."""
    text = render_content_text(content)
    return OutputStats(
        text=text,
        lines=text.count("\n") + 1 if text else 0,
        bytes=len(text.encode("utf-8")) if text else 0,
        preview=text[:preview_chars] if text else "",
    )


def render_content_text(content: Any) -> str:
    """Render content as plain text without exposing inline binary data."""
    normalized = normalize_content_parts(content)
    if normalized is None:
        return ""
    if isinstance(normalized, str):
        return normalized

    lines: list[str] = []
    for part in normalized:
        if isinstance(part, TextPart):
            if part.text:
                lines.append(part.text)
        elif isinstance(part, ImagePart):
            lines.append(_render_image_placeholder(part))
        elif isinstance(part, FilePart):
            lines.append(_render_file_placeholder(part))
    return "\n".join(line for line in lines if line)


def normalize_tool_output(
    output: Any,
    *,
    max_output: int,
    job_id: str = "",
    tool_name: str = "",
    artifact_store: Any = None,
    preview_chars: int = TOOL_OUTPUT_PREVIEW_CHARS,
    saved_to: str | None = None,
) -> NormalizedToolOutput:
    """Normalize and byte-limit a tool result before storage or injection.

    Inline images become session artifacts when possible and safe placeholders
    otherwise, preventing raw base64 from entering model context.
    """
    metadata: dict[str, Any] = {}
    normalized = normalize_content_parts(output)
    if normalized is None:
        normalized = ""

    if isinstance(normalized, str):
        truncated, trunc_meta = truncate_text_utf8(
            normalized, max_output, tool_name=tool_name, saved_to=saved_to
        )
        metadata.update(trunc_meta)
        stats = output_stats(truncated, preview_chars=preview_chars)
        return NormalizedToolOutput(output=truncated, stats=stats, metadata=metadata)

    parts: list[ContentPart] = []
    materialized = 0
    elided = 0
    for idx, part in enumerate(normalized):
        if isinstance(part, ImagePart):
            replacement = materialize_image_part(
                part,
                artifact_store,
                subdir="tool_outputs",
                stem_hint=_artifact_stem(tool_name, job_id, idx, part.source_name),
                elide_without_store=True,
            )
            if isinstance(replacement, ImagePart) and replacement.url != part.url:
                materialized += 1
            elif isinstance(replacement, TextPart):
                elided += 1
            parts.append(replacement)
        elif isinstance(part, FilePart):
            parts.append(_normalize_file_part(part))
        else:
            parts.append(part)

    parts, trunc_meta = _truncate_text_parts(
        parts, max_output, tool_name=tool_name, saved_to=saved_to
    )
    metadata.update(trunc_meta)
    if materialized:
        metadata["data_urls_materialized"] = materialized
    if elided:
        metadata["data_urls_elided"] = elided
    stats = output_stats(parts, preview_chars=preview_chars)
    return NormalizedToolOutput(output=parts, stats=stats, metadata=metadata)


def materialize_image_part(
    part: ImagePart,
    artifact_store: Any,
    *,
    subdir: str,
    stem_hint: str | None = None,
    elide_without_store: bool = False,
) -> ImagePart | TextPart:
    """Persist an inline image or return a safe non-binary replacement.

    Ordinary URLs pass through unchanged. Without storage, callers may retain the
    original part or request a placeholder through ``elide_without_store``.
    """
    match = _DATA_URL_RE.match(part.url or "")
    if not match:
        return part

    ext = match.group("ext").split(";", 1)[0].lower()
    b64 = match.group("b64")
    if artifact_store is None:
        if elide_without_store:
            return TextPart(text=_data_url_placeholder(part, ext, len(b64)))
        return part

    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception as e:
        logger.warning("Failed to decode image data URL", error=str(e))
        if elide_without_store:
            return TextPart(text=_data_url_placeholder(part, ext, len(b64)))
        return part

    safe_stem = _safe_name(
        stem_hint or part.source_name or f"img_{int(time.time() * 1000)}"
    )
    safe_ext = re.sub(r"[^\w]", "", ext) or "png"
    filename = f"{subdir}/{safe_stem}.{safe_ext}"
    try:
        disk_path = artifact_store.write_artifact(filename, raw)
    except Exception as e:
        logger.warning(
            "Failed to persist image artifact — falling back to placeholder",
            error=str(e),
        )
        if elide_without_store:
            return TextPart(text=_data_url_placeholder(part, ext, len(b64)))
        return part

    session_id = getattr(artifact_store, "session_id", "") or ""
    if session_id:
        served = f"/api/sessions/{session_id}/artifacts/{filename}"
    else:
        served = disk_path.as_uri()

    new_part = ImagePart(
        url=served,
        detail=part.detail,
        source_type=part.source_type,
        source_name=part.source_name,
    )
    _copy_dynamic_image_attrs(part, new_part)
    return new_part


def _truncate_text_parts(
    parts: list[ContentPart],
    max_output: int,
    *,
    tool_name: str = "",
    saved_to: str | None = None,
) -> tuple[list[ContentPart], dict[str, Any]]:
    text_bytes = sum(
        len(part.text.encode("utf-8")) for part in parts if isinstance(part, TextPart)
    )
    if max_output <= 0 or text_bytes <= max_output:
        return parts, {"truncated": False, "original_text_bytes": text_bytes}

    remaining = max_output
    omitted = 0
    out: list[ContentPart] = []
    note_added = False
    for part in parts:
        if not isinstance(part, TextPart):
            out.append(part)
            continue
        raw = part.text.encode("utf-8")
        if remaining <= 0:
            omitted += len(raw)
            continue
        if len(raw) <= remaining:
            out.append(part)
            remaining -= len(raw)
            continue
        prefix = raw[:remaining].decode("utf-8", errors="ignore")
        kept = len(prefix.encode("utf-8"))
        omitted += len(raw) - kept
        out.append(TextPart(text=prefix))
        remaining = 0

    if not note_added:
        out.append(
            TextPart(
                text=_truncation_note(
                    max_output, omitted, tool_name=tool_name, saved_to=saved_to
                )
            )
        )
    return (
        out,
        {
            "truncated": True,
            "original_text_bytes": text_bytes,
            "max_output_bytes": max_output,
            "omitted_text_bytes": omitted,
        },
    )


def _normalize_file_part(part: FilePart) -> ContentPart:
    if part.data_base64:
        label = part.name or part.path or "file"
        return TextPart(
            text=f"[file: {label}; base64 content elided ({len(part.data_base64)} chars)]"
        )
    return part


def _render_image_placeholder(part: ImagePart) -> str:
    match = _DATA_URL_RE.match(part.url or "")
    if match:
        return _data_url_placeholder(part, match.group("ext"), len(match.group("b64")))
    desc = part.get_description()
    url = part.url or ""
    if url:
        if len(url) > 500:
            url = url[:500] + "..."
        return f"{desc} {url}"
    return desc


def _render_file_placeholder(part: FilePart) -> str:
    label = part.name or part.path or "file"
    if part.content:
        return f"[file: {label}]\n{part.content}"
    if part.data_base64:
        return f"[file: {label}; base64 content elided ({len(part.data_base64)} chars)]"
    return f"[file: {label}]"


def _data_url_placeholder(part: ImagePart, ext: str, b64_len: int) -> str:
    desc = part.get_description()
    name = f" {part.source_name}" if part.source_name else ""
    return f"{desc}{name} data:image/{ext};base64 elided ({b64_len} chars)"


def _artifact_stem(
    tool_name: str, job_id: str, idx: int, source_name: str | None
) -> str:
    pieces = [p for p in (tool_name, job_id, str(idx), source_name or "image") if p]
    return "_".join(pieces) or f"tool_image_{int(time.time() * 1000)}"


def _safe_name(value: str) -> str:
    return re.sub(r"[^\w.-]", "_", value.strip()) or "artifact"


def _copy_dynamic_image_attrs(src: ImagePart, dst: ImagePart) -> None:
    for attr in ("revised_prompt",):
        if hasattr(src, attr):
            setattr(dst, attr, getattr(src, attr))
