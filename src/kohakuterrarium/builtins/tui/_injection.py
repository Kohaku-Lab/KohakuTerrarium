"""Mid-turn ``user_input_injected`` activity handler for the TUI.

Extracted from :mod:`builtins.tui.output` so the renderer file stays
within the 1000-line cap. The web shell does the equivalent in
``stores/chat.js:_handleUserInputInjected``; this is the TUI mirror.

Thread/loop safety: the activity is delivered from the agent's
controller-loop asyncio task (not Textual's own message pump). Every
widget mutation MUST be scheduled on Textual's loop via
``TUISession._safe_call`` (which wraps ``app.call_later``). Calling
``widget.remove()`` directly from the controller task creates a
coroutine on the wrong loop context and contributed to the TUI lock-up
the user reported when typing during processing — the entire promotion
+ mount is now a single ``_safe_call`` callback so Textual sees one
atomic update from its own pump.
"""

from typing import Any


def extract_injected_text(content: Any) -> str:
    """Resolve the typed text from a ``user_input_injected`` content
    field — string or OpenAI-shape list of content-part dicts."""
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
    """Promote a matching ``QueuedMessage`` widget to a ``UserMessage``
    when the backend folds a buffered input into the running turn.

    Both the queued-widget removal and the new ``UserMessage`` mount run
    inside ONE ``_safe_call`` callback so Textual processes them as a
    single render step from its own message pump. Calling either
    operation from the controller-loop task directly is unsafe.

    When no queue match (programmatic / trigger-fired injection), just
    append a fresh user bubble so the transcript stays consistent with
    what the agent sees on its next round.
    """
    if not tui:
        return
    text = extract_injected_text(metadata.get("content", ""))
    if not text:
        return

    def _promote_on_textual_loop() -> None:
        # All widget mutation must run here, inside Textual's loop, so
        # remove() + mount() see consistent DOM state.
        app = getattr(tui, "_app", None)
        widgets = getattr(app, "_queued_widgets", None) if app else None
        if widgets:
            match = next(
                (qw for qw in widgets if getattr(qw, "message_text", None) == text),
                None,
            )
            if match is not None:
                try:
                    match.remove()  # AwaitRemove — schedules removal on this loop
                except Exception:  # pragma: no cover - defensive
                    pass
                try:
                    widgets.remove(match)
                except ValueError:
                    pass
        tui.add_user_message(text, target=target)

    safe_call = getattr(tui, "_safe_call", None)
    if safe_call is None:
        # Test-shim path: callers without ``_safe_call`` (the recording
        # stubs in ``tests/integration/test_tui_cli_mid_turn_injection``)
        # run the body inline. Real TUISession ALWAYS has _safe_call.
        _promote_on_textual_loop()
        return
    safe_call(_promote_on_textual_loop)
