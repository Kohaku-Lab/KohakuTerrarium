"""Integration-shape tests for ``RichCLIApp.setup_multi_creature``.

We don't boot the prompt_toolkit Application (that needs a real PTY
and is gated to manual / e2e per design.md §9). What we DO test:

- Multi-creature setup populates the per-creature live regions +
  draft buffer + focus controller exactly once.
- ``_handle_creature_event`` routes text into the right
  ``LiveRegionState`` and bumps unread on non-focused creatures.
- ``focus_next`` / ``focus_prev`` swap the agent ref + reset unread
  on the new focus + preserve the draft of the old focus.
- ``resolve_creature_by_name`` finds creatures by id, name, prefix.
- ``inject_to_creature`` + ``broadcast_to_all`` honor the
  privileged-focus guard for ``@all``.
- The Multiplex sink → app handler round-trip stamps the right
  ``creature_id`` per event.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.cli_rich.app import RichCLIApp
from kohakuterrarium.builtins.cli_rich.multiplex import MultiplexedRichOutput
from kohakuterrarium.terrarium.events import EngineEvent, EventKind


class _FakeAgent:
    """Minimal Agent-shaped namespace for app setup."""

    def __init__(self, name: str):
        self.config = SimpleNamespace(name=name)
        self.llm = SimpleNamespace(model=f"model-for-{name}", _profile_max_context=0)
        self.input = None
        self.output_router = SimpleNamespace(default_output=None)
        self.injected: list[str] = []
        self._processing_task = None
        self._active_handles: dict = {}
        self._last_activity_ts = None
        self._last_generation_tokens = 0

    def llm_identifier(self):
        return self.llm.model

    async def inject_input(self, text, source="cli"):
        self.injected.append(text)


class _FakeCreature:
    def __init__(self, *, creature_id, name, privileged=False, running=True):
        self.creature_id = creature_id
        self.name = name
        self.agent = _FakeAgent(name)
        self.is_privileged = privileged
        self._running = running
        self.listen_channels: list = []
        self.send_channels: list = []
        self._last_turn_failed = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False


class _FakeEngine:
    def __init__(self, creatures):
        self._by_id = {c.creature_id: c for c in creatures}

    def get_creature(self, cid):
        if cid not in self._by_id:
            raise KeyError(cid)
        return self._by_id[cid]

    def list_creatures(self):
        return list(self._by_id.values())


@pytest.fixture
def app_and_engine():
    """Build a RichCLIApp wired to a 3-creature fake engine."""
    c1 = _FakeCreature(creature_id="c1", name="alice", privileged=True)
    c2 = _FakeCreature(creature_id="c2", name="bob")
    c3 = _FakeCreature(creature_id="c3", name="carol")
    engine = _FakeEngine([c1, c2, c3])
    app = RichCLIApp(c1.agent)
    app.setup_multi_creature(engine, "c1")
    return app, engine, [c1, c2, c3]


class TestSetup:
    def test_flag_flipped_on(self, app_and_engine):
        app, _, _ = app_and_engine
        assert app.multi_creature_enabled is True

    def test_focus_controller_has_every_creature(self, app_and_engine):
        app, _, _ = app_and_engine
        assert app.focus_controller.creature_ids == ["c1", "c2", "c3"]
        assert app.focus_controller.focus_id == "c1"

    def test_live_region_per_creature(self, app_and_engine):
        app, _, _ = app_and_engine
        assert set(app.live_regions) == {"c1", "c2", "c3"}

    def test_draft_buffer_per_creature(self, app_and_engine):
        app, _, _ = app_and_engine
        assert app.draft_by_creature == {"c1": "", "c2": "", "c3": ""}

    def test_agent_points_at_focus(self, app_and_engine):
        app, _, creatures = app_and_engine
        assert app.agent is creatures[0].agent

    def test_roster_and_overlay_constructed(self, app_and_engine):
        app, _, _ = app_and_engine
        assert app.roster is not None
        assert app.agent_overlay is not None
        assert app.peek_panel is not None


class TestHandleCreatureEventRouting:
    @pytest.mark.asyncio
    async def test_text_for_non_focus_bumps_unread(self, app_and_engine):
        app, _, _ = app_and_engine
        await app._handle_creature_event("c2", "text", {"text": "hello bob"})
        assert app.live_regions["c2"].text_buffer == "hello bob"
        assert app.live_regions["c2"].unread_since_focus == 1
        # Focused creature is untouched.
        assert app.live_regions["c1"].text_buffer == ""
        assert app.live_regions["c1"].unread_since_focus == 0

    @pytest.mark.asyncio
    async def test_text_for_focus_records_and_no_unread(self, app_and_engine):
        app, _, _ = app_and_engine
        await app._handle_creature_event("c1", "text", {"text": "for me"})
        assert app.live_regions["c1"].text_buffer == "for me"
        # Focus creature does not bump its own unread counter (it's
        # the one being watched).
        assert app.live_regions["c1"].unread_since_focus == 0

    @pytest.mark.asyncio
    async def test_unknown_creature_id_is_dropped(self, app_and_engine):
        app, _, _ = app_and_engine
        # Should not raise.
        await app._handle_creature_event("nobody", "text", {"text": "x"})


class TestFocusSwap:
    def test_focus_next_swaps_agent_and_resets_unread(self, app_and_engine):
        app, _, creatures = app_and_engine
        app.live_regions["c2"].unread_since_focus = 5
        app.focus_next()
        assert app.focus_controller.focus_id == "c2"
        assert app.agent is creatures[1].agent
        assert app.live_regions["c2"].unread_since_focus == 0

    def test_focus_prev_wraps(self, app_and_engine):
        app, _, _ = app_and_engine
        app.focus_prev()
        assert app.focus_controller.focus_id == "c3"

    def test_set_focus_jumps_directly(self, app_and_engine):
        app, _, _ = app_and_engine
        app.set_focus("c3")
        assert app.focus_controller.focus_id == "c3"

    def test_set_focus_unknown_is_noop(self, app_and_engine):
        app, _, _ = app_and_engine
        app.set_focus("nope")
        assert app.focus_controller.focus_id == "c1"


class TestResolveByName:
    def test_resolve_by_creature_id(self, app_and_engine):
        app, _, _ = app_and_engine
        assert app.resolve_creature_by_name("c2") is not None

    def test_resolve_by_name(self, app_and_engine):
        app, _, _ = app_and_engine
        c = app.resolve_creature_by_name("bob")
        assert c is not None and c.creature_id == "c2"

    def test_resolve_unknown_returns_none(self, app_and_engine):
        app, _, _ = app_and_engine
        assert app.resolve_creature_by_name("nope") is None

    def test_case_insensitive_prefix(self, app_and_engine):
        app, _, _ = app_and_engine
        c = app.resolve_creature_by_name("BO")
        assert c is not None and c.creature_id == "c2"


class TestInject:
    @pytest.mark.asyncio
    async def test_inject_to_creature_targets_specific_agent(self, app_and_engine):
        app, _, creatures = app_and_engine
        await app.inject_to_creature("c2", "hi bob")
        assert creatures[1].agent.injected == ["hi bob"]
        assert creatures[0].agent.injected == []
        assert creatures[2].agent.injected == []

    @pytest.mark.asyncio
    async def test_inject_unknown_creature_is_dropped(self, app_and_engine):
        app, _, _ = app_and_engine
        await app.inject_to_creature("nope", "x")  # no raise


class TestBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_from_privileged_focus_fans_to_all(self, app_and_engine):
        app, _, creatures = app_and_engine
        # Focus is c1 (privileged in fixture).
        await app.broadcast_to_all("hello everyone")
        for c in creatures:
            assert c.agent.injected == ["hello everyone"]

    @pytest.mark.asyncio
    async def test_broadcast_from_non_privileged_is_rejected(self, app_and_engine):
        app, _, creatures = app_and_engine
        # Switch focus to c2 (not privileged).
        app.set_focus("c2")
        await app.broadcast_to_all("denied?")
        for c in creatures:
            assert c.agent.injected == []


class _DynamicEngine(_FakeEngine):
    """Engine fake that lets tests add/remove creatures dynamically."""

    def add_creature(self, creature):
        self._by_id[creature.creature_id] = creature

    def drop_creature(self, creature_id):
        self._by_id.pop(creature_id, None)


class TestRuntimeGraphChanges:
    """Topic 08 — runtime creature add/remove keeps the rich CLI coherent."""

    def _build(self):
        c1 = _FakeCreature(creature_id="c1", name="alice", privileged=True)
        c2 = _FakeCreature(creature_id="c2", name="bob")
        engine = _DynamicEngine([c1, c2])
        app = RichCLIApp(c1.agent)
        app.setup_multi_creature(engine, "c1")
        for c in (c1, c2):
            app.mount_creature_sink(c)
        return app, engine, c1, c2

    def test_mount_sink_records_previous_output(self):
        app, _, c1, _ = self._build()
        # Setup mounted the focus creature's sink and recorded a
        # ``None`` previous (the FakeAgent starts with None).
        assert "c1" in app._managed_outputs
        assert isinstance(c1.agent.output_router.default_output, MultiplexedRichOutput)

    def test_mount_is_idempotent(self):
        app, _, c1, _ = self._build()
        before = app._managed_outputs["c1"]
        app.mount_creature_sink(c1)  # second call — same previous preserved
        assert app._managed_outputs["c1"] is before

    def test_restore_sink_puts_previous_output_back(self):
        app, _, c1, _ = self._build()
        app.restore_creature_sink("c1")
        assert "c1" not in app._managed_outputs
        # Original default_output was None on the FakeAgent.
        assert c1.agent.output_router.default_output is None

    def test_creature_started_spawns_state_and_sink(self):
        app, engine, _, _ = self._build()
        c3 = _FakeCreature(creature_id="c3", name="carol")
        engine.add_creature(c3)
        app._on_creature_started("c3")
        assert "c3" in app.live_regions
        assert "c3" in app.live_region_widgets
        assert "c3" in app._managed_outputs
        assert "c3" in app.focus_controller.creature_ids
        assert isinstance(c3.agent.output_router.default_output, MultiplexedRichOutput)

    def test_creature_started_is_idempotent(self):
        app, engine, _, _ = self._build()
        c3 = _FakeCreature(creature_id="c3", name="carol")
        engine.add_creature(c3)
        app._on_creature_started("c3")
        sink_first = c3.agent.output_router.default_output
        app._on_creature_started("c3")  # second call must not re-wrap
        assert c3.agent.output_router.default_output is sink_first

    def test_creature_stopped_drops_state(self):
        app, _, _, c2 = self._build()
        app._on_creature_stopped("c2")
        assert "c2" not in app.live_regions
        assert "c2" not in app.live_region_widgets
        assert "c2" not in app._managed_outputs
        assert "c2" not in app.focus_controller.creature_ids
        # Sink was reverted to the FakeAgent's original (None).
        assert c2.agent.output_router.default_output is None

    def test_creature_stopped_unknown_is_noop(self):
        app, _, _, _ = self._build()
        # Should not raise even for an id we never knew about.
        app._on_creature_stopped("nobody")

    def test_creature_stopped_on_focus_refocuses_sibling(self):
        app, _, c1, _ = self._build()
        # Focus is c1 — removing it should pick c2 as the new focus.
        app._on_creature_stopped("c1")
        assert app.focus_controller.focus_id == "c2"
        assert "c1" not in app.live_regions

    def test_creature_stopped_when_only_one_clears_live_region(self):
        app, engine, _, _ = self._build()
        app._on_creature_stopped("c2")
        app._on_creature_stopped("c1")
        assert app.focus_controller.focus_id == ""
        # ``self.live_region`` is reset to a fresh blank widget rather
        # than the now-dropped c1 widget.
        assert app.live_region is not None

    @pytest.mark.asyncio
    async def test_teardown_restores_every_managed_sink(self):
        app, _, c1, c2 = self._build()
        await app.teardown_multi_creature()
        assert c1.agent.output_router.default_output is None
        assert c2.agent.output_router.default_output is None
        assert app._managed_outputs == {}

    @pytest.mark.asyncio
    async def test_teardown_without_watcher_is_safe(self):
        app, _, _, _ = self._build()
        # We never called start_engine_watch, so the watch task is None;
        # teardown must still restore sinks without raising.
        await app.teardown_multi_creature()
        assert app._engine_watch_task is None

    @pytest.mark.asyncio
    async def test_watch_engine_dispatches_real_events(self):
        """End-to-end: subscribe() yields events → topology dispatch fires."""
        app, engine, _, _ = self._build()

        class _SubscribingEngine(_DynamicEngine):
            def __init__(self, base):
                super().__init__(list(base._by_id.values()))
                self.queue: asyncio.Queue = asyncio.Queue()

            async def subscribe(self, filt=None):
                while True:
                    ev = await self.queue.get()
                    if ev is None:
                        return
                    if filt is None or filt.matches(ev):
                        yield ev

        sub_engine = _SubscribingEngine(engine)
        # Re-wire the app onto the subscribing engine so the watcher
        # iterates against this queue, not the inert base fake.
        app.engine = sub_engine
        # The lookup paths the dispatch handlers use go through
        # ``sub_engine.get_creature`` which already knows c1/c2.
        app.start_engine_watch()

        c3 = _FakeCreature(creature_id="c3", name="carol")
        sub_engine.add_creature(c3)
        await sub_engine.queue.put(
            EngineEvent(kind=EventKind.CREATURE_STARTED, creature_id="c3")
        )
        # Yield control so the watcher task can drain the queue.
        for _ in range(10):
            await asyncio.sleep(0)
            if "c3" in app.live_regions:
                break
        assert "c3" in app.live_regions
        assert "c3" in app.focus_controller.creature_ids

        await sub_engine.queue.put(
            EngineEvent(kind=EventKind.CREATURE_STOPPED, creature_id="c3")
        )
        for _ in range(10):
            await asyncio.sleep(0)
            if "c3" not in app.live_regions:
                break
        assert "c3" not in app.live_regions

        await app.teardown_multi_creature()


class TestB2CaptureAndReplay:
    """B2 design: per-creature scrollback capture + on-focus-change redraw."""

    def _build(self):
        c1 = _FakeCreature(creature_id="c1", name="alice", privileged=True)
        c2 = _FakeCreature(creature_id="c2", name="bob")
        engine = _FakeEngine([c1, c2])
        app = RichCLIApp(c1.agent)
        app.setup_multi_creature(engine, "c1")
        return app, engine, c1, c2

    def test_initial_capture_target_is_focused_creature(self):
        app, _, _, _ = self._build()
        assert app.committer._capture_target == "c1"

    def test_text_commit_records_into_current_target(self):
        app, _, _, _ = self._build()
        app.committer.text("[red]hi[/red]")
        assert any(m == "text" for m, _ in app.committer.captured_for("c1"))
        assert app.committer.captured_for("c2") == []

    def test_user_message_commit_records(self):
        app, _, _, _ = self._build()
        app.committer.user_message("hello bob")
        assert ("user_message", ("hello bob",)) in app.committer.captured_for("c1")

    def test_capture_target_swap_during_creature_event(self):
        """A commit emitted DURING bob's event must land in bob's bucket."""
        app, _, _, _ = self._build()
        prev = app.committer._capture_target
        app.committer.set_capture_target("c2")
        try:
            app.committer.user_message("from bob's flow")
        finally:
            app.committer.set_capture_target(prev)
        assert ("user_message", ("from bob's flow",)) in app.committer.captured_for(
            "c2"
        )
        assert ("user_message", ("from bob's flow",)) not in app.committer.captured_for(
            "c1"
        )

    def test_replay_mode_suppresses_recapture(self):
        app, _, _, _ = self._build()
        app.committer.text("first")
        before = list(app.committer.captured_for("c1"))
        app.committer.set_replay_mode(True)
        try:
            app.committer.text("re-emit")
        finally:
            app.committer.set_replay_mode(False)
        assert app.committer.captured_for("c1") == before

    def test_clear_capture_drops_bucket(self):
        app, _, _, _ = self._build()
        app.committer.text("x")
        app.committer.clear_capture("c1")
        assert app.committer.captured_for("c1") == []

    def test_redraw_focused_replays_committed_log(self):
        app, _, _, _ = self._build()
        # Fill c2's bucket with two captured commits.
        app.committer.set_capture_target("c2")
        app.committer.user_message("question to bob")
        app.committer.text("bob's reply")
        app.committer.set_capture_target("c1")
        # Tab to c2 — _on_focus_changed should run redraw_focused
        # against c2's bucket. We pre-set the focus so we don't depend
        # on the full focus_next path.
        app.focus_controller.focus_id = "c2"

        called: list[tuple[str, tuple]] = []
        orig_user = app.committer.user_message
        orig_text = app.committer.text

        def trace_user(t):
            called.append(("user_message", (t,)))
            return orig_user(t)

        def trace_text(t):
            called.append(("text", (t,)))
            return orig_text(t)

        app.committer.user_message = trace_user
        app.committer.text = trace_text
        try:
            app.redraw_focused()
        finally:
            app.committer.user_message = orig_user
            app.committer.text = orig_text

        # Both captured items were replayed in order.
        assert ("user_message", ("question to bob",)) in called
        assert ("text", ("bob's reply",)) in called

    def test_redraw_does_not_double_capture(self):
        app, _, _, _ = self._build()
        app.committer.set_capture_target("c2")
        app.committer.user_message("once")
        app.committer.set_capture_target("c1")
        app.focus_controller.focus_id = "c2"
        before_len = len(app.committer.captured_for("c2"))
        app.redraw_focused()
        # Replay must NOT have re-appended the same item.
        assert len(app.committer.captured_for("c2")) == before_len

    def test_redraw_unknown_focus_is_safe(self):
        app, _, _, _ = self._build()
        app.focus_controller.focus_id = "ghost"
        # No bucket exists for "ghost"; should not raise.
        app.redraw_focused()

    def test_commit_user_message_for_lands_in_target_bucket(self):
        app, _, _, _ = self._build()
        # focus is c1; @bob redirect should commit into c2's bucket.
        app.commit_user_message_for("c2", "@bob hi")
        assert ("user_message", ("@bob hi",)) in app.committer.captured_for("c2")
        # And NOT in c1's bucket.
        assert ("user_message", ("@bob hi",)) not in app.committer.captured_for("c1")
        # Capture target restored to the original (c1).
        assert app.committer._capture_target == "c1"

    def test_commit_user_message_broadcast_fans_into_every_bucket(self):
        app, _, _, _ = self._build()
        app.commit_user_message_broadcast("@all hello")
        # Visible commit on focused bucket (c1) + duplicate in c2.
        assert ("user_message", ("@all hello",)) in app.committer.captured_for("c1")
        assert ("user_message", ("@all hello",)) in app.committer.captured_for("c2")

    def test_creature_stopped_drops_capture_bucket(self):
        app, _, _, _ = self._build()
        app.committer.set_capture_target("c2")
        app.committer.text("bob stuff")
        app.committer.set_capture_target("c1")
        app._on_creature_stopped("c2")
        assert app.committer.captured_for("c2") == []


class TestPerCreatureFooter:
    """Each per-creature LiveRegion widget must show its own agent's model."""

    def test_focus_widget_inherits_root_footer(self):
        c1 = _FakeCreature(creature_id="c1", name="alice")
        engine = _FakeEngine([c1])
        app = RichCLIApp(c1.agent)
        app.setup_multi_creature(engine, "c1")
        # Focus widget IS the app's initial live_region, footer was
        # initialized in __init__.
        widget = app.live_region_widgets["c1"]
        assert widget is app.live_region
        # Footer carries the agent's identifier.
        rendered = widget.footer._model
        assert rendered  # not empty

    def test_non_focus_widget_initialized_from_creature_agent(self):
        c1 = _FakeCreature(creature_id="c1", name="alice")
        c2 = _FakeCreature(creature_id="c2", name="bob")
        engine = _FakeEngine([c1, c2])
        app = RichCLIApp(c1.agent)
        app.setup_multi_creature(engine, "c1")
        bob_widget = app.live_region_widgets["c2"]
        assert bob_widget is not app.live_region
        # Bob's widget footer should carry bob's model now (not blank).
        assert bob_widget.footer._model == "model-for-bob"

    def test_focus_change_refreshes_footer_for_new_focus(self):
        c1 = _FakeCreature(creature_id="c1", name="alice")
        c2 = _FakeCreature(creature_id="c2", name="bob")
        engine = _FakeEngine([c1, c2])
        app = RichCLIApp(c1.agent)
        app.setup_multi_creature(engine, "c1")
        # Simulate corruption: blank out c2's footer.
        app.live_region_widgets["c2"].footer._model = ""
        # Switching focus repopulates it from the agent.
        app.set_focus("c2")
        assert app.live_region.footer._model == "model-for-bob"


class TestMultiplexEndToEnd:
    @pytest.mark.asyncio
    async def test_multiplex_sink_text_round_trip(self, app_and_engine):
        app, _, _ = app_and_engine
        sink = MultiplexedRichOutput(
            handler=app._handle_creature_event, creature_id="c2"
        )
        await sink.write("from bob's sink")
        # Bob isn't focused — the buffer + unread should reflect that.
        assert app.live_regions["c2"].text_buffer == "from bob's sink"
        assert app.live_regions["c2"].unread_since_focus == 1

    @pytest.mark.asyncio
    async def test_multiplex_processing_lifecycle_for_focus(self, app_and_engine):
        app, _, _ = app_and_engine
        sink = MultiplexedRichOutput(
            handler=app._handle_creature_event, creature_id="c1"
        )
        # Just verify these don't raise — they call into the live
        # region of the focus creature.
        await sink.on_processing_start()
        await sink.on_processing_end()
