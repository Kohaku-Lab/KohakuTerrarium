"""Shared Responses-API reasoning event collection.

Both Codex and the OpenAI-compatible WebSocket path speak the Responses
API; keep their reasoning stream handling in one place.
"""

from typing import Any

from kohakuterrarium.llm.turn_segments import TurnSegmentsBuilder


def _parts_text(parts: Any) -> str:
    """Join Responses reasoning content/summary parts into plain text."""
    if parts is None:
        return ""
    if isinstance(parts, str):
        return parts
    if isinstance(parts, dict):
        parts = [parts]
    if not isinstance(parts, list):
        return ""
    pieces: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            value = part.get("text") or part.get("content") or ""
        else:
            value = getattr(part, "text", None) or getattr(part, "content", None) or ""
        if isinstance(value, str) and value:
            pieces.append(value)
    return "\n".join(pieces)


def _join(existing: str, addition: str) -> str:
    return "\n".join(part for part in (existing, addition) if part)


class ResponsesReasoningCollector:
    """Accumulate Responses-API reasoning text and summary fragments."""

    def __init__(self) -> None:
        self.text = ""
        self.summary = ""
        self.segments = TurnSegmentsBuilder()
        self._seen_reasoning_item_ids: set[str] = set()

    def consume(self, event: Any) -> None:
        """Fold one Responses stream event into the collector."""
        match getattr(event, "type", ""):
            case "response.reasoning_text.delta":
                piece = getattr(event, "delta", None)
                if isinstance(piece, str):
                    self.text += piece
                    self.segments.append_reasoning(
                        piece,
                        source="responses_text",
                        key=getattr(event, "item_id", None),
                    )
            case "response.reasoning_summary_text.delta":
                piece = getattr(event, "delta", None)
                if isinstance(piece, str):
                    self.summary += piece
                    self.segments.append_reasoning(
                        piece,
                        source="responses_summary",
                        key=getattr(event, "item_id", None),
                    )
            case "response.reasoning_text.done":
                piece = getattr(event, "text", None) or getattr(event, "delta", None)
                if isinstance(piece, str) and piece:
                    self.text = piece
                    self.segments.replace_reasoning(
                        piece,
                        source="responses_text",
                        key=getattr(event, "item_id", None),
                    )
                    item_id = getattr(event, "item_id", None)
                    if isinstance(item_id, str) and item_id:
                        self._seen_reasoning_item_ids.add(item_id)
            case "response.reasoning_summary_text.done":
                piece = getattr(event, "text", None) or getattr(event, "delta", None)
                if isinstance(piece, str) and piece:
                    self.summary = piece
                    self.segments.replace_reasoning(
                        piece,
                        source="responses_summary",
                        key=getattr(event, "item_id", None),
                    )
                    item_id = getattr(event, "item_id", None)
                    if isinstance(item_id, str) and item_id:
                        self._seen_reasoning_item_ids.add(item_id)
            case "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", "") == "reasoning":
                    self.consume_item(item)

    def consume_item(self, item: Any) -> None:
        """Fold a completed ``reasoning`` output item into the collector."""
        item_id = getattr(item, "id", None)
        if isinstance(item_id, str) and item_id:
            if item_id in self._seen_reasoning_item_ids:
                return
            self._seen_reasoning_item_ids.add(item_id)
        summary = _parts_text(getattr(item, "summary", None))
        content = _parts_text(getattr(item, "content", None))
        if summary:
            self.summary = _join(self.summary, summary)
            self.segments.append_reasoning(
                summary, source="responses_summary", key=getattr(item, "id", None)
            )
        if content:
            self.text = _join(self.text, content)
            self.segments.append_reasoning(
                content, source="responses_text", key=getattr(item, "id", None)
            )

    def consume_output_text(self, piece: str) -> None:
        """Record visible output text in its arrival position."""
        if isinstance(piece, str) and piece:
            self.segments.append_text(piece)

    def consume_function_call(self, call_id: str) -> None:
        """Record a tool-call reference in its arrival position."""
        if call_id:
            self.segments.append_tool_call_ref(call_id)

    def fields(self) -> dict[str, Any]:
        """Return non-empty extra fields for conversation persistence."""
        packed: dict[str, Any] = {}
        if self.text:
            packed["reasoning_content"] = self.text
        if self.summary:
            packed["reasoning_summary"] = self.summary
        return self.segments.inject_into(packed)
