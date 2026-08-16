"""Ordered assistant-turn segment helpers.

Providers see reasoning, visible text, and tool calls as separate streams.
This module records their arrival order in a compact segment list stored
under ``extra_fields[_kt_assistant_segments]`` so UIs can render thinking
in place instead of as one disconnected blob.
"""

from typing import Any

KT_ASSISTANT_SEGMENTS = "_kt_assistant_segments"


def _coerce_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class TurnSegmentsBuilder:
    """Accumulate reasoning/text/tool-call segments in arrival order."""

    def __init__(self) -> None:
        self._segments: list[dict[str, Any]] = []

    def append_reasoning(
        self,
        text: Any,
        *,
        source: str,
        key: str | None = None,
        signature: str | None = None,
    ) -> None:
        piece = _coerce_text(text)
        if not piece and not signature:
            return
        last = self._segments[-1] if self._segments else None
        if (
            last is not None
            and last.get("type") == "reasoning"
            and last.get("source") == source
            and last.get("key") == key
        ):
            if piece:
                last["text"] = f"{last.get('text', '')}{piece}"
            if signature:
                last["signature"] = signature
            return
        segment: dict[str, Any] = {"type": "reasoning", "source": source}
        if key is not None:
            segment["key"] = key
        if piece:
            segment["text"] = piece
        else:
            segment["text"] = ""
        if signature:
            segment["signature"] = signature
        self._segments.append(segment)

    def append_text(self, text: Any) -> None:
        piece = _coerce_text(text)
        if not piece:
            return
        last = self._segments[-1] if self._segments else None
        if last is not None and last.get("type") == "text":
            last["text"] = f"{last.get('text', '')}{piece}"
            return
        self._segments.append({"type": "text", "text": piece})

    def replace_reasoning(
        self, text: Any, *, source: str, key: str | None = None
    ) -> None:
        """Replace matching reasoning segments with one complete segment."""
        self._segments = [
            segment
            for segment in self._segments
            if not (
                segment.get("type") == "reasoning"
                and segment.get("source") == source
                and segment.get("key") == key
            )
        ]
        self.append_reasoning(text, source=source, key=key)

    def append_tool_call_ref(self, call_id: str, index: int | None = None) -> None:
        if index is not None:
            for segment in reversed(self._segments):
                if (
                    segment.get("type") == "tool_call_ref"
                    and segment.get("index") == index
                ):
                    if call_id:
                        segment["call_id"] = call_id
                    return
        segment: dict[str, Any] = {"type": "tool_call_ref", "call_id": call_id}
        if index is not None:
            segment["index"] = index
        self._segments.append(segment)

    def finalize_tool_call_refs(self, pending_calls: dict[int, dict[str, Any]]) -> None:
        for segment in self._segments:
            if segment.get("type") != "tool_call_ref":
                continue
            index = segment.get("index")
            call = pending_calls.get(index) if isinstance(index, int) else None
            if call is not None:
                segment["call_id"] = call.get("id", "") or segment.get("call_id", "")

    def as_list(self) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for segment in self._segments:
            if segment.get("type") == "tool_call_ref":
                call_id = segment.get("call_id")
                if not call_id:
                    continue
                segments.append({"type": "tool_call_ref", "call_id": call_id})
                continue
            segments.append(dict(segment))
        return segments

    def inject_into(self, fields: dict[str, Any]) -> dict[str, Any]:
        segments = self.as_list()
        if not segments:
            return fields
        # A plain text/tool exchange without reasoning already has a stable
        # representation; only emit segments when they add ordering value.
        if not any(segment.get("type") == "reasoning" for segment in segments):
            return fields
        merged = dict(fields)
        merged[KT_ASSISTANT_SEGMENTS] = segments
        return merged


def segments_from_anthropic_blocks(blocks: Any) -> list[dict[str, Any]]:
    """Translate native Anthropic content blocks into ordered segments."""
    segments: list[dict[str, Any]] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in {"thinking", "redacted_thinking"}:
            text = block.get("thinking") or block.get("data") or ""
            signature = block.get("signature") or ""
            segment: dict[str, Any] = {
                "type": "reasoning",
                "source": f"anthropic_{btype}",
                "text": text,
            }
            if signature:
                segment["signature"] = signature
            segments.append(segment)
        elif btype == "text":
            text = block.get("text") or ""
            if text:
                segments.append({"type": "text", "text": text})
        elif btype == "tool_use":
            call_id = block.get("id") or ""
            if call_id:
                segments.append({"type": "tool_call_ref", "call_id": call_id})
    return segments


def inject_anthropic_segments(fields: dict[str, Any], blocks: Any) -> dict[str, Any]:
    """Add ordered Anthropic segments to extra fields when thinking exists."""
    segments = segments_from_anthropic_blocks(blocks)
    if not any(segment.get("type") == "reasoning" for segment in segments):
        return fields
    merged = dict(fields)
    merged[KT_ASSISTANT_SEGMENTS] = segments
    return merged
