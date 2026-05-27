"""Mid-turn user-input injection workflow — TUI + Rich CLI renderers.

Feat 3 (opportunistic input injection) routes a buffered ``user_input``
through the agent's ``output_router.notify_activity("user_input_injected",
...)`` so EACH renderer can display the typed message in its chat
transcript. The web frontend has its own dispatch; this file pins the
TUI + Rich CLI behaviour with one end-to-end workflow per renderer.

Each test drives the **full activity dispatch path** the production code
runs — building the renderer with a captured-call session/app stub,
firing the activity through the renderer's ``on_activity_with_metadata``
entry point (the same call site ``OutputRouter._dispatch_activity_event``
uses), and asserting both:

  - the rendered chat receives the user message text (so the user sees
    the bubble appear when the backend drains the buffer)
  - the optional pre-existing queued widget is promoted (TUI keeps a
    ``QueuedMessage`` above the input while processing; it must remove
    that placeholder when the canonical render lands)
"""

from typing import Any

from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput
from kohakuterrarium.builtins.tui.output import TUIOutput


class _RecordingTUISession:
    """Minimal stub mirroring ``TUISession`` for renderer dispatch tests.

    Records every ``add_user_message`` call so the test can assert that
    a ``user_input_injected`` activity surfaced as a chat bubble — the
    user-visible signal the web shell already produces.
    """

    def __init__(self) -> None:
        self.user_messages: list[tuple[str, str]] = []
        self._app = _RecordingTUIApp()

    def add_user_message(self, text: str, target: str = "") -> None:
        self.user_messages.append((text, target))

    # No-op stubs for everything else ``TUIOutput`` may poke at.
    def end_streaming(self, target: str = "") -> None: ...
    def start_thinking(self) -> None: ...
    def stop_thinking(self) -> None: ...
    def set_idle(self) -> None: ...
    def append_stream(self, chunk: str, target: str = "") -> None: ...
    def begin_streaming(self, target: str = "") -> None: ...


class _RecordingQueuedWidget:
    """Stand-in for the Textual ``QueuedMessage`` widget. Tracks removal
    so the test can verify the matching queued placeholder is taken
    down when the canonical message lands."""

    def __init__(self, text: str) -> None:
        self.message_text = text
        self.removed = False

    def remove(self) -> None:
        self.removed = True


class _RecordingTUIApp:
    """Minimal ``AgentTUI`` stand-in. The renderer reaches into
    ``app._queued_widgets`` to promote a matching placeholder."""

    def __init__(self) -> None:
        self._queued_widgets: list[_RecordingQueuedWidget] = []


class _RecordingCLIApp:
    """Stand-in for ``RichCLIApp``. ``RichCLIOutput._dispatch`` calls
    ``app._commit_user_message`` to render a user-message line; record
    every call so the test can assert mid-turn injection surfaced."""

    def __init__(self) -> None:
        self.user_messages: list[str] = []

    def _commit_user_message(self, text: str) -> None:
        self.user_messages.append(text)


class TestMidTurnInjectionRenderers:
    """One feature workflow per renderer: activity arrives → user
    message bubble appears (and any matching queued placeholder gets
    promoted)."""

    def test_tui_user_input_injected_renders_and_promotes_queued_widget(self) -> None:
        # Setup: a TUIOutput wired against the recording session +
        # one queued ``QueuedMessage`` widget that mirrors what
        # ``AgentTUI.on_input_submitted`` puts up when the user types
        # during processing.
        output = TUIOutput(session_key="test_agent")
        session = _RecordingTUISession()
        output._tui = session  # _on_start would normally do this
        queued = _RecordingQueuedWidget("Hello mid-turn")
        session._app._queued_widgets.append(queued)

        # Drive: a user_input_injected activity with the list-shape
        # content the WS path emits ([{"type": "text", "text": ...}]).
        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {
                "content": [{"type": "text", "text": "Hello mid-turn"}],
                "turn_index": 1,
                "branch_id": 1,
            },
        )

        # Assertions: the queued placeholder was removed AND a real
        # user message landed in the chat. Both are user-visible
        # signals that mid-turn injection finally surfaced.
        assert queued.removed is True
        assert queued not in session._app._queued_widgets
        assert session.user_messages == [("Hello mid-turn", "")]

    def test_tui_user_input_injected_string_content_renders_without_queue(self) -> None:
        # Programmatic / trigger-fired injection has no queued widget;
        # the renderer should STILL surface the message in the chat
        # so the transcript stays consistent with what the agent's
        # next round will see.
        output = TUIOutput(session_key="test_agent")
        session = _RecordingTUISession()
        output._tui = session

        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {"content": "timer fired", "turn_index": 1, "branch_id": 1},
        )

        assert session.user_messages == [("timer fired", "")]
        # Queue stays empty (it was empty to begin with).
        assert session._app._queued_widgets == []

    def test_tui_user_input_injected_ignores_empty_content(self) -> None:
        # Defensive: empty content (an edge case from filtered triggers)
        # MUST NOT emit a blank user bubble — that would clutter the
        # transcript with no information.
        output = TUIOutput(session_key="test_agent")
        session = _RecordingTUISession()
        output._tui = session

        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {"content": "", "turn_index": 1, "branch_id": 1},
        )
        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {"content": None, "turn_index": 1, "branch_id": 1},
        )

        assert session.user_messages == []

    def test_cli_rich_user_input_injected_commits_user_message_line(self) -> None:
        # Setup: a RichCLIOutput wired against a recording app. The
        # output module exposes ``_dispatch`` (the same entrypoint
        # ``on_activity_with_metadata`` flows through).
        app = _RecordingCLIApp()
        output = RichCLIOutput.__new__(RichCLIOutput)
        output.app = app  # type: ignore[attr-defined]

        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {
                "content": [{"type": "text", "text": "Hello mid-turn"}],
                "turn_index": 1,
                "branch_id": 1,
            },
        )

        # The CLI renderer commits a user-message line so the
        # transcript shows what the agent saw on its next round —
        # mirroring the web shell's queue-pop behaviour.
        assert app.user_messages == ["Hello mid-turn"]

    def test_cli_rich_user_input_injected_string_content(self) -> None:
        # String-shape content (programmatic injection or trigger
        # prompt_override) should also render verbatim.
        app = _RecordingCLIApp()
        output = RichCLIOutput.__new__(RichCLIOutput)
        output.app = app  # type: ignore[attr-defined]

        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {"content": "timer fired", "turn_index": 1, "branch_id": 1},
        )

        assert app.user_messages == ["timer fired"]

    def test_tui_widget_mutations_routed_through_safe_call(self) -> None:
        # SAFETY contract: when a TUI has ``_safe_call``, the handler
        # MUST schedule queued-widget removal + new-message mount inside
        # ONE ``_safe_call`` callback so Textual sees an atomic update
        # from its own loop. Calling ``widget.remove()`` directly from
        # the controller-loop asyncio task contributed to the TUI
        # lock-up the user hit when typing during processing.
        from kohakuterrarium.builtins.tui._injection import (
            handle_user_input_injected,
        )

        scheduled: list[str] = []

        class _RecordingSafeCallTUI:
            def __init__(self) -> None:
                self._app = _RecordingTUIApp()
                self._app._queued_widgets.append(
                    _RecordingQueuedWidget("Hello mid-turn")
                )
                self.user_messages: list[tuple[str, str]] = []

            def _safe_call(self, fn: Any) -> None:
                # Record the callback shape rather than invoking it
                # synchronously — this proves the handler defers the
                # entire mutation to Textual's loop.
                scheduled.append(fn.__name__ if hasattr(fn, "__name__") else "callback")
                fn()  # in the test we DO invoke so downstream assertions hold

            def add_user_message(self, text: str, target: str = "") -> None:
                self.user_messages.append((text, target))

        tui = _RecordingSafeCallTUI()
        handle_user_input_injected(
            tui,
            {
                "content": [{"type": "text", "text": "Hello mid-turn"}],
                "turn_index": 1,
                "branch_id": 1,
            },
            target="main",
        )

        # The handler scheduled exactly ONE callback on Textual's loop —
        # not multiple uncoordinated mutations.
        assert len(scheduled) == 1, (
            f"handler must route ALL widget mutations through one _safe_call; "
            f"saw {len(scheduled)}"
        )
        # And the callback did the right thing (queue cleared + user
        # message mounted).
        assert tui._app._queued_widgets == []
        assert tui.user_messages == [("Hello mid-turn", "main")]

    def test_cli_rich_user_input_injected_ignores_empty(self) -> None:
        # Empty content must NOT commit a blank user-message line —
        # the CLI live region would render a stray empty row.
        app = _RecordingCLIApp()
        output = RichCLIOutput.__new__(RichCLIOutput)
        output.app = app  # type: ignore[attr-defined]

        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {"content": "", "turn_index": 1, "branch_id": 1},
        )
        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {"content": [], "turn_index": 1, "branch_id": 1},
        )

        assert app.user_messages == []
