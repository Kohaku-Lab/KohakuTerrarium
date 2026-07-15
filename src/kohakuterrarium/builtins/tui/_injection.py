"""Render mid-turn injected input without crossing Textual loop boundaries.

Queued input promotion removes the queued widget and mounts its user message in
one scheduled callback so both mutations observe a consistent DOM state.
"""

from typing import Any


def extract_injected_text(content: Any) -> str:
    """Extract text from string or structured injected content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def handle_user_input_injected(tui: Any, metadata: dict, target: str) -> None:
    """Promote matching queued input atomically or append programmatic input."""
    if not tui:
        return
    text = extract_injected_text(metadata.get("content", ""))
    if not text:
        return

    def _promote_on_textual_loop() -> None:
        # Removal and mounting must share Textual's loop and DOM state.
        app = getattr(tui, "_app", None)
        widgets = getattr(app, "_queued_widgets", None) if app else None
        if widgets:
            match = next(
                (qw for qw in widgets if getattr(qw, "message_text", None) == text),
                None,
            )
            if match is not None:
                try:
                    match.remove()
                except Exception:  # pragma: no cover - defensive boundary
                    pass
                try:
                    widgets.remove(match)
                except ValueError:
                    pass
        tui.add_user_message(text, target=target)

    safe_call = getattr(tui, "_safe_call", None)
    if safe_call is None:
        # Lightweight callers without a scheduler execute synchronously.
        _promote_on_textual_loop()
        return
    safe_call(_promote_on_textual_loop)
