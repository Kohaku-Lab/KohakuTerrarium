"""Shared Responses-API reasoning event collection.

Both Codex and the OpenAI-compatible WebSocket path speak the Responses
API; keep their reasoning stream handling in one place.
"""

from typing import Any


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

    def consume(self, event: Any) -> None:
        """Fold one Responses stream event into the collector."""
        match getattr(event, "type", ""):
            case "response.reasoning_text.delta":
                piece = getattr(event, "delta", None)
                if isinstance(piece, str):
                    self.text += piece
            case "response.reasoning_summary_text.delta":
                piece = getattr(event, "delta", None)
                if isinstance(piece, str):
                    self.summary += piece
            case "response.reasoning_text.done":
                piece = getattr(event, "text", None) or getattr(event, "delta", None)
                if isinstance(piece, str) and piece:
                    self.text = piece
            case "response.reasoning_summary_text.done":
                piece = getattr(event, "text", None) or getattr(event, "delta", None)
                if isinstance(piece, str) and piece:
                    self.summary = piece
            case "response.output_item.added" | "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", "") == "reasoning":
                    self.consume_item(item)

    def consume_item(self, item: Any) -> None:
        """Fold a completed ``reasoning`` output item into the collector."""
        summary = _parts_text(getattr(item, "summary", None))
        content = _parts_text(getattr(item, "content", None))
        if summary:
            self.summary = _join(self.summary, summary)
        if content:
            self.text = _join(self.text, content)

    def fields(self) -> dict[str, Any]:
        """Return non-empty extra fields for conversation persistence."""
        packed: dict[str, Any] = {}
        if self.text:
            packed["reasoning_content"] = self.text
        if self.summary:
            packed["reasoning_summary"] = self.summary
        return packed
