"""Regression suite for production UI bugs reproduced by the user.

The file started as a repro log; the current assertions cover the fixed
behaviour for Rich CLI/TUI mid-turn input handling and keep a few explicit
source-order/edge-case guards so future changes do not reintroduce the
freezes, queue stalls, or dead terminal bindings.
"""

import asyncio
import contextlib
from typing import Any

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.output import OutputRecorder


async def _make_agent(tmp_path, llm_holder):
    """Build a minimal agent for backend-only repro tests."""
    cfg = AgentConfig(
        name="repro",
        llm_profile="openai/gpt-4-test",
        model="gpt-4",
        provider="openai",
        api_key_env="",
        system_prompt="Test agent.",
        include_tools_in_prompt=False,
        include_hints_in_prompt=False,
        tool_format="bracket",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="stdout"),
        tools=[],
    )
    agent = Agent(cfg)
    recorder = OutputRecorder()
    agent.output_router.default_output = recorder
    return agent, recorder


class TestRichCliCancelOnSecondInput:
    """Low-level guard for why Rich CLI must not cancel plain follow-ups.

    Cancelling the wrapper task around ``agent.inject_input(...)`` still
    propagates into the active controller loop and emits an ``interrupt``
    activity. The production fix below is therefore to avoid that cancel
    path for normal mid-turn text submits; slash/control commands keep it.
    """

    async def test_cancel_of_inject_task_emits_interrupt_activity(
        self, tmp_path, monkeypatch
    ):
        """Mimic Rich CLI's _handle_submit:
        - Task 1 wraps ``await agent.inject_input("A")`` — first turn
        - User submits "B" → CLI cancels Task 1 (the wrapper) BEFORE
          spawning Task 2.
        - Agent emits ``interrupt`` activity instead of buffering B.
        """
        # Use a ScriptedLLM that streams slowly so we can interrupt mid-turn.
        from kohakuterrarium.bootstrap import agent_init as _ainit
        from kohakuterrarium.bootstrap import llm as _bllm
        from kohakuterrarium.testing.llm import ScriptEntry

        # 3 chunks * 0.1s ≈ 0.3s window during which we can cancel.
        scripted = ScriptedLLM(
            [
                ScriptEntry(
                    response="hello world reply", chunk_size=3, delay_per_chunk=0.1
                )
            ]
        )

        def _fake_create(*args, **kwargs):
            return scripted

        monkeypatch.setattr(_bllm, "create_llm_provider", _fake_create)
        monkeypatch.setattr(_ainit, "create_llm_provider", _fake_create)

        agent, recorder = await _make_agent(tmp_path, scripted)
        await agent.start()
        try:
            # Task 1: first user input, awaited by a wrapper task — the
            # exact shape ``cli_rich/app.py:_send()`` creates.
            wrapper_task = asyncio.create_task(agent.inject_input("A", source="cli"))

            # Wait until processing has actually started (LLM streaming).
            for _ in range(50):
                if agent._processing_task is not None:
                    break
                await asyncio.sleep(0.01)
            assert agent._processing_task is not None, "first turn never started"

            # Simulate Rich CLI's "user typed a second message" — the
            # CLI's _handle_submit calls _pending_task.cancel() on the
            # wrapper, NOT agent.inject_input("B"). The second inject
            # would happen AFTER, but the cancel comes FIRST.
            wrapper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await wrapper_task

            # Wait for the agent to finish its cancellation handling.
            for _ in range(50):
                if agent._processing_task is None:
                    break
                await asyncio.sleep(0.01)

            # Guard: wrapper cancellation still emits ``interrupt``. The
            # Rich CLI fix is to avoid this path for plain mid-turn text
            # submits, not to mask the lower-level cancellation signal.
            interrupts = recorder.activities_of_type("interrupt")
            assert len(interrupts) >= 1, (
                "Cancelling the inject_input wrapper task still propagates "
                "into the agent's controller loop and emits 'interrupt'; "
                "plain mid-turn text submits must avoid cancelling it."
            )

            # AND the agent's mid-turn buffer was never engaged — the
            # cancel happened at the wrapper level before any second
            # ``inject_input`` call could observe the processing lock.
            assert agent._pending_mid_turn_inputs == [], (
                "Buffer never engaged — confirms the cancellation path "
                "skips the buffering logic entirely."
            )
        finally:
            await agent.stop()


class TestRichCliMidTurnInjectionFix:
    """Verifies the fix for the Rich CLI bug above.

    ``_handle_submit`` no longer cancels the in-flight ``_pending_task``
    when the agent is actively processing a turn. Plain-text follow-up
    submits route through ``self._mid_turn_inject(text)`` which calls
    ``agent.inject_input`` WITHOUT touching ``_pending_task``. The
    agent's ``_pending_mid_turn_inputs`` buffer absorbs the input and
    the drain emits a ``user_input_injected`` activity that
    ``RichCLIOutput._dispatch`` already handles (commits a user-message
    line so the CLI transcript stays in sync).

    Slash commands and ``@name`` routing keep the original
    cancel-and-spawn path so they take precedence.
    """

    async def test_mid_turn_text_submit_does_NOT_cancel_pending_task(
        self, tmp_path, monkeypatch
    ):
        """The user's regression: typing a second plain-text message
        while the first is streaming used to cancel the first turn and
        surface ``⚠ interrupted``. After the fix it routes through
        ``_mid_turn_inject`` and DOES NOT touch ``_pending_task``."""
        from kohakuterrarium.bootstrap import agent_init as _ainit
        from kohakuterrarium.bootstrap import llm as _bllm
        from kohakuterrarium.builtins.cli_rich.app import RichCLIApp
        from kohakuterrarium.testing.llm import ScriptEntry

        scripted = ScriptedLLM(
            [ScriptEntry(response="ok reply", chunk_size=2, delay_per_chunk=0.05)]
        )

        def _fake_create(*args, **kwargs):
            return scripted

        monkeypatch.setattr(_bllm, "create_llm_provider", _fake_create)
        monkeypatch.setattr(_ainit, "create_llm_provider", _fake_create)

        agent, _recorder = await _make_agent(tmp_path, scripted)
        await agent.start()

        # Real RichCLIApp wired to the real agent.
        cli_app = RichCLIApp(agent)

        # Simulate the in-flight first turn — _processing=True and a
        # _pending_task wrapping the inject_input await.
        cli_app._processing = True

        async def _holdover_first_turn():
            # Stand-in for the first turn's _send wrapper. Just sleep
            # so the test can observe whether the FIX preserves it.
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                raise

        cli_app._pending_task = asyncio.ensure_future(_holdover_first_turn())
        original_pending = cli_app._pending_task

        # Stub the committer so _commit_user_message doesn't try to
        # render Rich panels in the test (no real terminal).
        committed: list[str] = []

        class _FakeCommitter:
            def text(self, *_a, **_kw):
                pass

            def user_message(self, text: str) -> None:
                committed.append(text)

            def flush_block_close(self) -> None:
                pass

            def blank_line(self) -> None:
                pass

        cli_app.committer = _FakeCommitter()
        cli_app._invalidate = lambda: None

        try:
            # User types a second message mid-stream — this is the
            # exact code path that USED to cancel the in-flight turn.
            cli_app._handle_submit("follow-up question")

            # FIX assertion 1: the in-flight pending task is UNTOUCHED.
            assert cli_app._pending_task is original_pending
            assert not original_pending.cancelled()
            assert not original_pending.done()

            # FIX assertion 2: the message is QUEUED in the live region,
            # NOT committed to chat history. The user must see their
            # input was registered ("(queued)" indicator) while the
            # canonical user-message line is held back until the agent's
            # drain emits ``user_input_injected``.
            assert "follow-up question" in cli_app.live_region._queued_inputs
            # And explicitly NOT committed to scrollback yet.
            assert "follow-up question" not in committed

            # Yield once so the spawned _mid_turn_inject task has a
            # chance to call agent.inject_input — which the agent
            # buffers because _processing_lock is not held in this
            # test (we never started a real turn). Whether it buffers
            # or runs is OK; the critical invariants are pending-task
            # survival + queue-not-history routing.
            await asyncio.sleep(0)

        finally:
            original_pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await original_pending
            await agent.stop()

    async def test_user_input_injected_moves_queued_to_committed(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: a queued message stays in the live region's
        ``_queued_inputs`` until the agent's drain emits a matching
        ``user_input_injected`` activity. At that point, the canonical
        user-message line is committed to scrollback AND the queued
        indicator is removed. Verifies the dispatch in
        ``RichCLIOutput._dispatch``.
        """
        from kohakuterrarium.bootstrap import agent_init as _ainit
        from kohakuterrarium.bootstrap import llm as _bllm
        from kohakuterrarium.builtins.cli_rich.app import RichCLIApp
        from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput

        scripted = ScriptedLLM(["ok"])

        def _fake_create(*args, **kwargs):
            return scripted

        monkeypatch.setattr(_bllm, "create_llm_provider", _fake_create)
        monkeypatch.setattr(_ainit, "create_llm_provider", _fake_create)

        agent, _ = await _make_agent(tmp_path, scripted)
        cli_app = RichCLIApp(agent)
        cli_app._processing = True

        committed: list[str] = []

        class _FakeCommitter:
            def text(self, *_a, **_kw):
                pass

            def user_message(self, text: str) -> None:
                committed.append(text)

            def flush_block_close(self) -> None:
                pass

            def blank_line(self) -> None:
                pass

        cli_app.committer = _FakeCommitter()
        cli_app._invalidate = lambda: None

        # Simulate user submit (mid-turn path adds to queue).
        cli_app.live_region.add_queued_input("hello mid-turn")
        assert cli_app.live_region._queued_inputs == ["hello mid-turn"]
        assert committed == []

        # Now simulate the agent's drain emitting the activity. Build
        # the output module the way the agent does and dispatch.
        output = RichCLIOutput.__new__(RichCLIOutput)
        output.app = cli_app  # type: ignore[attr-defined]
        output.on_activity_with_metadata(
            "user_input_injected",
            "",
            {
                "content": [{"type": "text", "text": "hello mid-turn"}],
                "turn_index": 1,
                "branch_id": 1,
            },
        )

        # Queued indicator gone + canonical line committed to scrollback.
        assert cli_app.live_region._queued_inputs == []
        assert committed == ["hello mid-turn"]

        await agent.stop()

    async def test_slash_command_DOES_cancel_pending(self, tmp_path, monkeypatch):
        """Slash commands keep the original cancel-and-spawn path so
        ``/exit``, ``/help``, ``/model`` etc. take precedence over an
        in-flight turn — the user explicitly invoked a control command
        and shouldn't have to wait for the LLM to finish streaming."""
        from kohakuterrarium.bootstrap import agent_init as _ainit
        from kohakuterrarium.bootstrap import llm as _bllm
        from kohakuterrarium.builtins.cli_rich.app import RichCLIApp

        scripted = ScriptedLLM(["ok"])

        def _fake_create(*args, **kwargs):
            return scripted

        monkeypatch.setattr(_bllm, "create_llm_provider", _fake_create)
        monkeypatch.setattr(_ainit, "create_llm_provider", _fake_create)

        agent, _recorder = await _make_agent(tmp_path, scripted)
        await agent.start()
        cli_app = RichCLIApp(agent)
        cli_app._processing = True

        async def _holdover():
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                raise

        cli_app._pending_task = asyncio.ensure_future(_holdover())
        original_pending = cli_app._pending_task

        class _FakeCommitter:
            def text(self, *_a, **_kw):
                pass

            def user_message(self, text: str) -> None:
                pass

            def flush_block_close(self) -> None:
                pass

            def blank_line(self) -> None:
                pass

        cli_app.committer = _FakeCommitter()
        cli_app._invalidate = lambda: None
        # Stub the slash dispatcher so we don't actually run /help.
        cli_app._handle_slash = lambda text: asyncio.sleep(0)  # type: ignore[assignment]

        try:
            cli_app._handle_submit("/help")
            # Original pending was cancelled (slash takes precedence).
            await asyncio.sleep(0)
            assert original_pending.cancelled() or original_pending.done()
            # A NEW pending task was spawned for the slash command.
            assert cli_app._pending_task is not original_pending
        finally:
            if not original_pending.done():
                original_pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await original_pending
            if cli_app._pending_task and not cli_app._pending_task.done():
                cli_app._pending_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, BaseException):
                    await cli_app._pending_task
            await agent.stop()


class TestDrainYieldsToEventLoop:
    """The drain loop yields the event loop between iterations so
    renderers whose handlers schedule callbacks via ``call_later``
    actually get a render slot. Previously the loop fired N synchronous
    ``notify_activity`` calls without yielding — Textual's call_later
    queue backed up while the controller task held the loop, freezing
    input. (Reported as ``./temp/tui-freeze-investigation.md`` chain
    1: synchronous drain burst on the controller task.)
    """

    async def test_drain_yields_after_each_notify_activity(self, tmp_path, monkeypatch):
        from kohakuterrarium.bootstrap import agent_init as _ainit
        from kohakuterrarium.bootstrap import llm as _bllm
        from kohakuterrarium.core.events import TriggerEvent

        scripted = ScriptedLLM(["ok"])

        def _fake_create(*args, **kwargs):
            return scripted

        monkeypatch.setattr(_bllm, "create_llm_provider", _fake_create)
        monkeypatch.setattr(_ainit, "create_llm_provider", _fake_create)

        agent, _ = await _make_agent(tmp_path, scripted)
        await agent.start()
        try:
            # Buffer 5 events into the mid-turn queue.
            for i in range(5):
                agent._pending_mid_turn_inputs.append(
                    TriggerEvent(type="user_input", content=f"msg-{i}")
                )

            # Spy on notify_activity to track call order, AND inject a
            # small "other coroutine" that should get a slice if the
            # drain yields properly. Without yielding, the other
            # coroutine never runs until drain finishes.
            other_task_runs: list[int] = []

            async def _other_task() -> None:
                for i in range(10):
                    await asyncio.sleep(0)
                    other_task_runs.append(i)

            other = asyncio.ensure_future(_other_task())

            notify_count = [0]
            original_notify = agent.output_router.notify_activity

            def spy_notify(activity_type, detail, metadata=None):
                notify_count[0] += 1
                return original_notify(activity_type, detail, metadata)

            agent.output_router.notify_activity = spy_notify  # type: ignore[assignment]

            await agent._drain_mid_turn_pending_inputs(agent.controller)

            # Drain emitted 5 activities.
            assert notify_count[0] == 5

            # The other coroutine got at least ONE slice INTERLEAVED with
            # the drain (because each notify_activity yields). Without
            # the fix, other_task_runs would be empty until the drain
            # finishes.
            # The other coroutine got at least 4 slices INTERLEAVED
            # with the drain (one per notify_activity yield, minus one
            # for setup ordering). Without the fix, other_task_runs
            # would be empty until the drain finishes.
            assert len(other_task_runs) >= 4, (
                "drain must yield after each notify_activity so peer "
                f"coroutines (Textual render loop, etc.) get scheduled; "
                f"saw {len(other_task_runs)} peer ticks during a 5-event drain"
            )

            other.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await other
        finally:
            await agent.stop()


class TestTUISafeCallBuffersPreReadyCalls:
    """``_safe_call`` previously silently dropped widget mutations when
    ``app.is_running`` was False (early startup, before ``on_mount``).
    After the fix it buffers them and replays once the app is ready.
    """

    def test_pre_ready_calls_are_buffered_and_replayed(self):
        from kohakuterrarium.builtins.tui.session import TUISession

        replayed: list[str] = []

        class _FakeApp:
            def __init__(self):
                self.is_running = False

            def call_later(self, fn, *args):
                replayed.append(fn.__name__ if hasattr(fn, "__name__") else "cb")
                fn(*args)

        session = TUISession.__new__(TUISession)
        session._app = _FakeApp()
        session._pending_safe_calls = []

        def _do_a():
            replayed.append("A")

        def _do_b():
            replayed.append("B")

        # App not running — calls buffer.
        session._safe_call(_do_a)
        session._safe_call(_do_b)
        assert replayed == []
        assert len(session._pending_safe_calls) == 2

        # App becomes ready, next safe_call drains the buffer + runs new.
        session._app.is_running = True

        def _do_c():
            replayed.append("C")

        session._safe_call(_do_c)

        # All three landed (A and B from the buffer, then C).
        assert "A" in replayed
        assert "B" in replayed
        assert "C" in replayed
        # Buffer was cleared.
        assert session._pending_safe_calls == []


class TestTuiStdinStealFix:
    """Root cause of "TUI fully not interactable, ctrl+c can't exit":
    the agent's configured ``CLIInput`` was started by ``add_creature``
    BEFORE the Textual app launched. ``CLIInput.get_input`` runs
    ``sys.stdin.readline`` in an executor thread, consuming stdin bytes
    that Textual was supposed to see. Result: Textual renders the
    layout but every keypress / mouse event / Ctrl+C goes to the
    executor thread and is dropped.

    The fix matches the rich-CLI runner's pattern:
    1. ``cli/run.py`` defers ``creature.start()`` for ``--mode tui`` so
       the input module hasn't been spawned yet.
    2. ``run_engine_with_tui`` swaps any stdin-stealing input
       (``CLIInput`` / ``NonBlockingCLIInput`` / ``TUIInput``) to
       ``NoneInput`` before starting the creature. Textual's stdin
       reader becomes the sole consumer; the engine's own
       ``while True: text = await tui.get_input()`` loop drives input
       via ``focus.inject_input(...)`` so the agent doesn't need its
       own stdin reader.
    3. On exit, original input is restored.
    """

    def test_input_steals_stdin_classification(self):
        from kohakuterrarium.builtins.inputs.cli import (
            CLIInput,
            NonBlockingCLIInput,
        )
        from kohakuterrarium.builtins.inputs.none import NoneInput
        from kohakuterrarium.builtins.tui.input import TUIInput
        from kohakuterrarium.terrarium.engine_cli import _input_steals_stdin

        # Stdin-stealing inputs that would race Textual.
        assert _input_steals_stdin(CLIInput()) is True
        assert _input_steals_stdin(NonBlockingCLIInput()) is True
        # TUIInput would spawn ANOTHER Textual app — also a conflict.
        assert _input_steals_stdin(TUIInput()) is True
        # Non-terminal inputs are fine.
        assert _input_steals_stdin(NoneInput()) is False

    def test_cli_run_defers_creature_start_for_tui_mode(self):
        # Pin the deferred-start: cli/run.py:159 passes
        # ``start=(io_mode not in ("cli", "tui"))`` so the focus
        # creature is NOT started during ``engine.add_creature(...)``
        # in TUI mode. Without this, the creature's CLIInput task
        # spawns a blocking ``sys.stdin.readline`` BEFORE Textual
        # can claim stdin — the TUI then renders but never receives
        # input, the canonical symptom of the bug.
        from pathlib import Path

        run_py = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "kohakuterrarium"
            / "cli"
            / "run.py"
        )
        text = run_py.read_text(encoding="utf-8")
        # The condition MUST defer for BOTH cli and tui. A regression
        # to ``start=(io_mode != "cli")`` reintroduces the freeze.
        assert 'start=(io_mode not in ("cli", "tui"))' in text, (
            "cli/run.py must defer creature.start() for BOTH cli and tui "
            "modes — otherwise the agent's CLIInput grabs stdin before "
            "Textual can, and the TUI is born inert."
        )

    def test_tui_output_on_start_preserves_external_tui_wiring(self):
        # Regression: when ``engine_cli.run_engine_with_tui`` direct-
        # assigns ``focus_output._tui = tui`` and THEN starts the
        # creature, ``output_router.start()`` calls
        # ``TUIOutput._on_start``. The previous body unconditionally
        # did ``self._tui = session.tui``, **overwriting** the direct
        # assignment with a freshly-created (and unmounted) TUISession.
        # The visible AgentTUI lived on engine_cli's tui; the agent's
        # output went to the secondary session with no AgentTUI →
        # screen rendered but agent responses never appeared. User
        # reported as "TUI input never triggers agent". The fix in
        # ``_on_start`` (output.py) guards: if ``self._tui`` is already
        # set, don't clobber it.
        import asyncio as _asyncio

        from kohakuterrarium.builtins.tui.output import TUIOutput

        class _SentinelSession:
            """Stand-in for the engine_cli-created TUISession."""

            def __init__(self):
                self.marker = "engine_cli_session"

        output = TUIOutput(session_key="creature_a")
        external_session = _SentinelSession()
        output._tui = external_session  # ← engine_cli's direct wiring

        # Trigger _on_start the way ``output_router.start()`` does.
        _asyncio.run(output._on_start())

        # The direct assignment MUST be preserved. A regression to
        # unconditional reassignment would point ``output._tui`` at a
        # different (auto-created) TUISession.
        assert output._tui is external_session, (
            "TUIOutput._on_start must not clobber an externally-wired _tui; "
            "this is the entire reason the engine pre-wires it before "
            "starting the creature."
        )

    def test_handle_tui_slash_opens_model_picker_for_bare_slash_model(self):
        # Regression: ``/model`` (no args) MUST open the Textual model
        # picker modal. Before the input swap, ``TUIInput.try_user_command``
        # intercepted this; after swapping to ``NoneInput`` the
        # interception was lost and ``/model`` only printed text output
        # (the user-reported "settings/commands wire to nothing"
        # regression). The fix moved the dispatch to
        # ``engine_cli._handle_tui_slash`` (mirroring how
        # ``cli_rich/app.py:RichCLIApp._handle_slash`` does it).
        import asyncio as _asyncio

        from kohakuterrarium.terrarium.engine_cli import _handle_tui_slash

        modal_calls: list[tuple[str, Any]] = []

        class _RecordingTUI:
            async def show_model_picker_modal(self, agent: Any) -> None:
                modal_calls.append(("model_picker", agent))

            async def show_modules_modal(self, agent: Any) -> None:
                modal_calls.append(("modules", agent))

            async def show_module_edit_modal(self, agent: Any, name: str) -> bool:
                modal_calls.append(("module_edit", (agent, name)))
                return True

        class _DummyAgent:
            pass

        tui = _RecordingTUI()
        agent = _DummyAgent()
        handled = _asyncio.run(_handle_tui_slash("/model", tui, agent))
        assert handled is True
        assert modal_calls == [("model_picker", agent)]

    def test_handle_tui_slash_opens_modules_modal(self):
        # ``/module`` and ``/modules`` open the modules modal.
        import asyncio as _asyncio

        from kohakuterrarium.terrarium.engine_cli import _handle_tui_slash

        for variant in ("/module", "/modules", "/mod"):
            modal_calls: list[str] = []

            class _RecordingTUI:
                async def show_model_picker_modal(self, agent: Any) -> None:
                    modal_calls.append("model_picker")

                async def show_modules_modal(self, agent: Any) -> None:
                    modal_calls.append("modules")

                async def show_module_edit_modal(self, agent: Any, name: str) -> bool:
                    modal_calls.append(f"module_edit:{name}")
                    return True

            tui = _RecordingTUI()
            handled = _asyncio.run(_handle_tui_slash(variant, tui, object()))
            assert handled is True
            assert modal_calls == [
                "modules"
            ], f"variant {variant!r} should open the modules modal"

    def test_tui_wiring_order_pins_post_start_on_interrupt_binding(self):
        # Regression: ``tui._app.on_interrupt = focus.interrupt`` MUST
        # land AFTER ``await tui.start()`` because ``tui.start`` is
        # what creates ``tui._app``. The previous code did this BEFORE
        # ``tui.start()`` with an ``if tui._app:`` guard that always
        # evaluated to False — making ESC permanently dead (the
        # ``action_interrupt`` handler reads ``self.on_interrupt`` and
        # silently returns when it's None). This test pins the source
        # ordering so a regression is caught at CI time.
        from pathlib import Path

        engine_cli_py = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "kohakuterrarium"
            / "terrarium"
            / "engine_cli.py"
        )
        text = engine_cli_py.read_text(encoding="utf-8")
        start_idx = text.index("await tui.start()")
        # The on_interrupt wiring MUST appear AFTER tui.start().
        binding_idx = text.index("tui._app.on_interrupt = focus.interrupt")
        assert binding_idx > start_idx, (
            "engine_cli must wire ``tui._app.on_interrupt`` AFTER "
            "``await tui.start()`` — that's when ``tui._app`` exists. "
            "Doing it before makes ESC permanently inert."
        )

    def test_tui_host_agent_published_for_f2_f3_modals(self):
        # Regression: F2 (modules modal) and F3 (model picker modal)
        # handlers in ``AgentTUI`` read
        # ``self.tui_session.host_agent`` and silently return when it
        # is None. In the OLD design ``TUIInput.set_user_commands``
        # set this attribute; after the input swap to NoneInput
        # (stdin-contention fix), engine_cli must publish the focus
        # agent itself, or F2/F3 do nothing.
        from pathlib import Path

        engine_cli_py = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "kohakuterrarium"
            / "terrarium"
            / "engine_cli.py"
        )
        text = engine_cli_py.read_text(encoding="utf-8")
        assert "tui.host_agent = focus" in text, (
            "engine_cli must publish ``tui.host_agent = focus`` so the "
            "F2 / F3 modal action handlers can reach the live agent."
        )

    def test_tui_main_loop_uses_fire_and_forget_inject(self):
        # Regression: the engine's input loop MUST NOT ``await
        # focus.inject_input(text)`` inline. Awaiting blocks the loop
        # for the entire turn — a second user message that arrives
        # mid-turn sits in ``_input_queue`` forever (the main loop
        # can't loop back to read it). With fire-and-forget via
        # ``asyncio.create_task(focus.inject_input(...))``, the second
        # inject call reaches the agent immediately;
        # ``_process_event`` finds the lock held and routes it into
        # ``_pending_mid_turn_inputs`` for the current turn's drain.
        from pathlib import Path

        engine_cli_py = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "kohakuterrarium"
            / "terrarium"
            / "engine_cli.py"
        )
        text = engine_cli_py.read_text(encoding="utf-8")
        # The submit path must use ``_spawn_inject(...)`` (or
        # equivalent fire-and-forget), NOT a bare
        # ``await focus.inject_input(text, source="tui")`` inline.
        # Scope the assertion to the main loop region of the file so
        # the helper-function definitions (still ``await`` shaped)
        # aren't false positives.
        try_idx = text.index(
            "try:\n        while True:\n            text = await tui.get_input()"
        )
        loop_block = text[try_idx : try_idx + 3000]
        assert "_spawn_inject(focus.inject_input(text" in loop_block, (
            "engine_cli's main input loop must fire-and-forget "
            "``focus.inject_input(text, ...)`` so mid-turn buffering "
            "actually kicks in. Awaiting inline blocks the loop and "
            "the second message can never reach the agent's buffer."
        )
        # Strict guard: the bare-await form must NOT appear in the
        # loop block.
        assert "await focus.inject_input(text, source=" not in loop_block, (
            "Regression: the main loop went back to ``await "
            "focus.inject_input(text, ...)`` inline — this defeats "
            "mid-turn injection."
        )

    def test_handle_tui_slash_falls_through_for_other_commands(self):
        # Other slash commands (e.g. ``/help``, ``/clear``) MUST fall
        # through to the agent's standard slash dispatch by returning
        # False, so the engine's main loop runs ``focus.inject_input``.
        import asyncio as _asyncio

        from kohakuterrarium.terrarium.engine_cli import _handle_tui_slash

        class _NoModalTUI:
            async def show_model_picker_modal(self, agent: Any) -> None:
                raise AssertionError("should not be called for /help")

            async def show_modules_modal(self, agent: Any) -> None:
                raise AssertionError("should not be called for /help")

            async def show_module_edit_modal(self, agent: Any, name: str) -> bool:
                raise AssertionError("should not be called for /help")

        # Bare /model is the modal path; with args it falls through.
        for variant in ("/help", "/clear", "/exit", "/status"):
            handled = _asyncio.run(_handle_tui_slash(variant, _NoModalTUI(), object()))
            assert (
                handled is False
            ), f"{variant!r} should fall through to agent slash dispatch"

    def test_run_engine_with_tui_publishes_session_under_creature_keys(self):
        # Regression: ``run_engine_with_tui`` must publish its
        # TUISession under each routed creature's session_key BEFORE
        # ``creature.start()`` fires. Otherwise the FIRST output that
        # boots (TUIOutput) does ``session.tui = TUISession(...)`` and
        # creates a second session, then sets its own ``_tui`` to that
        # new session — bypassing the direct assignment engine_cli
        # made and leaving the agent's output stream going to the wrong
        # session (the one without an AgentTUI mounted).
        from pathlib import Path

        engine_cli_py = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "kohakuterrarium"
            / "terrarium"
            / "engine_cli.py"
        )
        text = engine_cli_py.read_text(encoding="utf-8")
        # The pre-publish loop must exist and run BEFORE the
        # ``await creature.start()`` loop.
        assert "session_for_creature = get_session(creature.creature_id)" in text
        assert "session_for_creature.tui = tui" in text
        pub_idx = text.index("session_for_creature.tui = tui")
        start_idx = text.index("await creature.start()")
        assert pub_idx < start_idx, (
            "engine_cli must publish the TUISession under each creature's "
            "session_key BEFORE starting any creature — otherwise "
            "TUIOutput._on_start auto-creates a second TUISession and "
            "the engine's pre-wiring is lost."
        )


class TestWidgetMountFromBackgroundTaskHypothesis:
    """TUI freeze hypothesis: the mid-turn drain calls
    ``tui.add_user_message`` from the backend's asyncio task (the
    controller loop). The widget mount path runs through
    ``TUISession._safe_call`` (session.py:113-130) which uses
    ``call_later`` / ``call_from_thread`` — these only work CORRECTLY
    when the Textual app is running and not blocked.

    If the call lands in a window where Textual is between renders OR
    the asyncio loop the widget queues for is not the same as
    Textual's internal loop, the mount sits forever and the user's
    terminal locks up.

    We can't fully reproduce Textual's deadlock in a unit test
    (Textual needs a real terminal). But we CAN document the call
    surface and confirm the helper invokes the safe-mount path.
    """

    def test_handle_user_input_injected_routes_through_safe_mount(self):
        """Just pin the call surface: ``handle_user_input_injected``
        ALWAYS ends in ``tui.add_user_message(text, target=...)``,
        which is a non-async function that schedules a widget mount on
        a separate thread/loop. Called from a backend asyncio task
        (the drain inside _collect_and_push_feedback), this places
        widget mutation onto Textual's loop while the controller
        coroutine continues to hold the agent's processing_lock and
        run subsequent rounds — a classic mount-while-render race.
        """
        from kohakuterrarium.builtins.tui._injection import (
            handle_user_input_injected,
        )

        class _RecordingTUI:
            def __init__(self):
                self.calls: list[tuple[str, str]] = []
                self._app = None

            def add_user_message(self, text: str, target: str = "") -> None:
                self.calls.append((text, target))

        tui = _RecordingTUI()
        # Simulate drain emitting against the running turn.
        handle_user_input_injected(
            tui,
            {
                "content": [{"type": "text", "text": "mid-turn typed"}],
                "turn_index": 1,
                "branch_id": 1,
            },
            target="main",
        )

        # Confirmed: a single add_user_message call lands. In production
        # this call originates from the controller loop's asyncio task,
        # NOT Textual's main thread. The freeze occurs when widget
        # mutation collides with the in-flight Textual render.
        assert tui.calls == [("mid-turn typed", "main")]
