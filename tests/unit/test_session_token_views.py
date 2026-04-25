"""Wave G — token-usage read API on :class:`SessionStore` + :class:`Agent`.

Renamed from ``test_session_token_aggregation.py``. Keeps the Wave A
baseline assertions (parent counters, separation from sub-agents) so
regressions on the storage side stay covered, and layers the Wave G
reader API on top:

* :meth:`SessionStore.token_usage` — per-namespace counters with
  opt-in ``include_subagents`` / ``include_attached`` / ``by_turn``.
* :meth:`SessionStore.token_usage_all_loops` — flat enumeration of
  every controller loop in the tree (main + sub-agents + attached).

Audit decision Q2 in ``plans/session-system/implementation-plan.md``
is the guiding constraint: no aggregation by default. Callers display
the views they want; the read API doesn't sum anything silently.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.session.attach import attach_agent_to_session
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.session import Session
from kohakuterrarium.session.store import SessionStore


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "tokens.kohakutr")
    s.init_meta(
        session_id="tokens",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["parent"],
    )
    yield s
    s.close()


def _emit_token_usage(
    output: SessionOutput,
    *,
    prompt: int,
    completion: int,
    total: int | None = None,
    cached: int = 0,
) -> None:
    """Simulate the controller emitting a token_usage activity."""
    output.on_activity_with_metadata(
        "token_usage",
        "",
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total if total is not None else prompt + completion,
            "cached_tokens": cached,
        },
    )


def _emit_subagent_done(
    output: SessionOutput,
    *,
    name: str,
    prompt: int,
    completion: int,
    total: int,
    cached: int = 0,
) -> None:
    """Simulate the parent seeing a sub-agent's completion event."""
    output.on_activity_with_metadata(
        "subagent_done",
        f"[{name}] finished",
        {
            "job_id": f"{name}_1",
            "result": "done",
            "turns": 3,
            "tools_used": ["grep", "read"],
            "duration": 1.0,
            "total_tokens": total,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cached_tokens": cached,
        },
    )


class TestParentTokenAccumulation:
    """Parent controller's own token_usage events accumulate correctly."""

    def test_parent_tokens_recorded_per_call(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        _emit_token_usage(output, prompt=100, completion=20, cached=10)
        _emit_token_usage(output, prompt=150, completion=30, cached=40)

        events = [e for e in store.get_events("parent") if e["type"] == "token_usage"]
        assert len(events) == 2
        assert events[0]["prompt_tokens"] == 100
        assert events[0]["completion_tokens"] == 20
        assert events[0]["cached_tokens"] == 10
        assert events[1]["prompt_tokens"] == 150
        assert events[1]["cached_tokens"] == 40

    def test_parent_cumulative_totals_in_state(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        _emit_token_usage(output, prompt=100, completion=20, cached=10)
        _emit_token_usage(output, prompt=150, completion=30, cached=40)

        usage = store.load_token_usage("parent")
        assert usage["total_input_tokens"] == 250
        assert usage["total_output_tokens"] == 50
        assert usage["total_cached_tokens"] == 50


class TestSubAgentTokensAreSeparate:
    """Sub-agent tokens are recorded on their own event, not merged into parent."""

    def test_subagent_result_event_carries_its_own_tokens(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        # Parent's own direct LLM call.
        _emit_token_usage(output, prompt=100, completion=20)
        # Sub-agent completion reported back to parent.
        _emit_subagent_done(
            output, name="explore", prompt=500, completion=80, total=580
        )

        events = store.get_events("parent")
        sub_result = [e for e in events if e["type"] == "subagent_result"][0]
        # Sub-agent tokens appear on the subagent_result event itself.
        assert sub_result["prompt_tokens"] == 500
        assert sub_result["completion_tokens"] == 80
        assert sub_result["total_tokens"] == 580

    def test_parent_state_does_not_double_count_subagent(self, store):
        """Parent's cumulative state reflects ONLY parent LLM calls."""
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        _emit_token_usage(output, prompt=100, completion=20)
        _emit_subagent_done(
            output, name="explore", prompt=500, completion=80, total=580
        )
        _emit_token_usage(output, prompt=200, completion=30)

        usage = store.load_token_usage("parent")
        # Parent counters cover 100 + 200 = 300 input, 20 + 30 = 50 output.
        # The sub-agent's 500 / 80 must NOT be rolled into parent.
        assert usage["total_input_tokens"] == 300
        assert usage["total_output_tokens"] == 50


class TestSubAgentTokensSurfacedForTokenViews:
    """Wave G: the ``token_usage_all_loops`` API surfaces each loop.

    Renamed from ``TestSubAgentTokensSurfacedForAggregation``. The Wave
    A baseline asserted that manual summation over events reproduced a
    tree total; Wave G replaces that with an explicit read API so
    consumers no longer have to know the event schema.
    """

    def _seed_runs(self, store, parent_name, pairs):
        """Pre-register sub-agent run metadata in the store.

        The read API enumerates ``subagents`` table rows to assign run
        indices, so tests need a minimal meta row per run. ``pairs`` is
        ``[(name, count), ...]``.
        """
        for name, count in pairs:
            for _ in range(count):
                run = store.next_subagent_run(parent_name, name)
                store.save_subagent(
                    parent=parent_name,
                    name=name,
                    run=run,
                    meta={"task": "stub", "turns": 0},
                )

    def test_all_loops_enumerates_main_and_subagents(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        # Register sub-agent runs in the store before emitting their
        # result events — mirrors ``SubAgentManager`` ordering.
        self._seed_runs(store, "parent", [("explore", 1), ("plan", 1)])

        _emit_token_usage(output, prompt=100, completion=20)
        _emit_subagent_done(
            output, name="explore", prompt=500, completion=80, total=580
        )
        _emit_subagent_done(output, name="plan", prompt=300, completion=40, total=340)
        _emit_token_usage(output, prompt=200, completion=30)

        loops = store.token_usage_all_loops()
        loops_map = dict(loops)

        # Main loop: own counters only.
        assert loops_map["parent"]["prompt_tokens"] == 300
        assert loops_map["parent"]["completion_tokens"] == 50

        # Sub-agents keyed by <parent>:subagent:<name>:<run>.
        assert "parent:subagent:explore:0" in loops_map
        assert loops_map["parent:subagent:explore:0"]["prompt_tokens"] == 500
        assert loops_map["parent:subagent:explore:0"]["completion_tokens"] == 80
        assert "parent:subagent:plan:0" in loops_map
        assert loops_map["parent:subagent:plan:0"]["prompt_tokens"] == 300
        assert loops_map["parent:subagent:plan:0"]["completion_tokens"] == 40

    def test_manual_aggregation_matches_all_loops_totals(self, store):
        """Replacement for the Wave A manual summation check.

        Consumers that want the old "session total" can sum
        ``token_usage_all_loops()``. This test documents that the sum
        still matches the old hand-coded walk over events.
        """
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        self._seed_runs(store, "parent", [("explore", 1), ("plan", 1)])

        _emit_token_usage(output, prompt=100, completion=20)
        _emit_subagent_done(
            output, name="explore", prompt=500, completion=80, total=580
        )
        _emit_subagent_done(output, name="plan", prompt=300, completion=40, total=340)
        _emit_token_usage(output, prompt=200, completion=30)

        total_prompt = sum(u["prompt_tokens"] for _, u in store.token_usage_all_loops())
        total_completion = sum(
            u["completion_tokens"] for _, u in store.token_usage_all_loops()
        )
        # Parent 300 + explore 500 + plan 300 = 1100 prompt tokens.
        assert total_prompt == 1100
        # Parent 50 + explore 80 + plan 40 = 170 completion tokens.
        assert total_completion == 170


class TestCachedTokensInParentState:
    """Cached tokens on parent calls flow into cumulative state."""

    def test_cumulative_cached_tracked(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        _emit_token_usage(output, prompt=100, completion=20, cached=60)
        _emit_token_usage(output, prompt=100, completion=20, cached=90)

        usage = store.load_token_usage("parent")
        assert usage["total_cached_tokens"] == 150


# ─── Wave G additions ────────────────────────────────────────────────


def _seed_subagent_meta(store, parent, name, count):
    """Helper mirroring SubAgentManager.spawn's meta-row registration."""
    for _ in range(count):
        run = store.next_subagent_run(parent, name)
        store.save_subagent(parent=parent, name=name, run=run, meta={"task": "stub"})


class _StubOut:
    """OutputModule stub — enough for OutputRouter + attach wiring."""

    async def start(self):
        pass

    async def stop(self):
        pass

    async def write(self, text):
        pass

    async def write_stream(self, chunk):
        pass

    async def flush(self):
        pass

    async def on_processing_start(self):
        pass

    async def on_processing_end(self):
        pass

    def on_activity(self, activity_type, detail):
        pass

    def on_activity_with_metadata(self, activity_type, detail, metadata):
        pass


def _stub_agent(name):
    router = OutputRouter(default_output=_StubOut(), named_outputs={})
    return SimpleNamespace(
        config=SimpleNamespace(name=name),
        output_router=router,
        session_store=None,
    )


class TestTokenUsageOwnOnly:
    """``SessionStore.token_usage(agent=...)`` without flags."""

    def test_own_counters_only(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        _emit_token_usage(output, prompt=100, completion=20, cached=5)
        _emit_subagent_done(
            output, name="explore", prompt=500, completion=80, total=580, cached=40
        )

        usage = store.token_usage("parent")
        # Only parent's own controller counters; sub-agent data absent.
        assert usage["agent"] == "parent"
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 20
        assert usage["cached_tokens"] == 5
        assert "subagents" not in usage
        assert "attached" not in usage
        assert "by_turn" not in usage

    def test_none_agent_raises(self, store):
        with pytest.raises(ValueError):
            store.token_usage(None)


class TestTokenUsageIncludeSubagents:
    """``include_subagents=True`` returns a nested ``subagents`` dict."""

    def test_includes_each_run_keyed_by_path(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        _seed_subagent_meta(store, "parent", "explore", 2)

        _emit_token_usage(output, prompt=100, completion=20)
        _emit_subagent_done(
            output, name="explore", prompt=500, completion=80, total=580
        )
        _emit_subagent_done(
            output, name="explore", prompt=250, completion=40, total=290, cached=20
        )

        usage = store.token_usage("parent", include_subagents=True)
        subs = usage["subagents"]
        assert "parent:subagent:explore:0" in subs
        assert "parent:subagent:explore:1" in subs
        assert subs["parent:subagent:explore:0"]["prompt_tokens"] == 500
        assert subs["parent:subagent:explore:1"]["prompt_tokens"] == 250
        assert subs["parent:subagent:explore:1"]["cached_tokens"] == 20

    def test_empty_subagents_returns_empty_dict(self, store):
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        _emit_token_usage(output, prompt=100, completion=20)

        usage = store.token_usage("parent", include_subagents=True)
        # Key is present and empty (not missing), per brief's contract.
        assert usage["subagents"] == {}


class TestTokenUsageIncludeAttached:
    """``include_attached=True`` surfaces attached-agent namespaces."""

    def test_attached_agent_counters_appear(self, tmp_path):
        host_store = SessionStore(tmp_path / "host.kohakutr.v2")
        host_store.init_meta(
            session_id="host",
            config_type="agent",
            config_path="/tmp",
            pwd=str(tmp_path),
            agents=["host"],
        )
        try:
            host_agent = _stub_agent("host")
            session = Session(host_store, agent=host_agent, name="host")
            helper = _stub_agent("helper")
            attach_agent_to_session(helper, session, role="reader")

            # Attached agent emits its own token_usage through the
            # secondary SessionOutput sink — goes under
            # ``host:attached:reader:0``.
            helper.output_router.notify_activity(
                "token_usage",
                "",
                metadata={
                    "prompt_tokens": 400,
                    "completion_tokens": 60,
                    "total_tokens": 460,
                    "cached_tokens": 10,
                },
            )

            usage = host_store.token_usage("host", include_attached=True)
            assert "attached" in usage
            assert "host:attached:reader:0" in usage["attached"]
            att = usage["attached"]["host:attached:reader:0"]
            assert att["prompt_tokens"] == 400
            assert att["completion_tokens"] == 60
            assert att["cached_tokens"] == 10
        finally:
            host_store.close(update_status=False)

    def test_zero_attached_returns_empty_dict(self, store):
        # No attach ever happened → ``attached`` key present, empty.
        usage = store.token_usage("parent", include_attached=True)
        assert usage["attached"] == {}


class TestTokenUsageByTurn:
    """``by_turn=True`` returns per-turn rows."""

    def test_by_turn_uses_rollup_table_when_populated(self, store):
        # Populate rollup table directly — simulates Wave B's
        # turn_rollup emitter (not yet wired in every code path).
        store.save_turn_rollup(
            "parent",
            1,
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "cached_tokens": 0,
            },
        )
        store.save_turn_rollup(
            "parent",
            2,
            {
                "prompt_tokens": 200,
                "completion_tokens": 40,
                "cached_tokens": 60,
            },
        )

        usage = store.token_usage("parent", by_turn=True)
        rows = usage["by_turn"]
        assert [r["turn_index"] for r in rows] == [1, 2]
        assert rows[0]["prompt"] == 100
        assert rows[1]["prompt"] == 200
        assert rows[1]["cached"] == 60

    def test_by_turn_falls_back_to_events_when_rollup_empty(self, store):
        # Inject token_usage events with turn_index set directly —
        # simulates what a future Wave-B emitter wiring will do. Wave
        # G's fallback path groups these by ``turn_index``.
        store.append_event(
            "parent",
            "token_usage",
            {"prompt_tokens": 50, "completion_tokens": 10, "cached_tokens": 0},
            turn_index=1,
        )
        store.append_event(
            "parent",
            "token_usage",
            {"prompt_tokens": 60, "completion_tokens": 12, "cached_tokens": 5},
            turn_index=1,
        )
        store.append_event(
            "parent",
            "token_usage",
            {"prompt_tokens": 30, "completion_tokens": 9, "cached_tokens": 0},
            turn_index=2,
        )

        usage = store.token_usage("parent", by_turn=True)
        rows = usage["by_turn"]
        assert [r["turn_index"] for r in rows] == [1, 2]
        assert rows[0]["prompt"] == 110  # 50 + 60
        assert rows[0]["completion"] == 22
        assert rows[0]["cached"] == 5
        assert rows[1]["prompt"] == 30


class TestTokenUsageAllLoops:
    """End-to-end: nested tree of main + sub-agents + attached."""

    def test_enumerates_main_subagent_attached_paths(self, tmp_path):
        host_store = SessionStore(tmp_path / "tree.kohakutr.v2")
        host_store.init_meta(
            session_id="tree",
            config_type="agent",
            config_path="/tmp",
            pwd=str(tmp_path),
            agents=["host"],
        )
        try:
            host_stub = _stub_agent("host")
            host_out = SessionOutput("host", host_store, host_stub)

            # Main counters + one sub-agent run.
            _emit_token_usage(host_out, prompt=200, completion=40, cached=10)
            _seed_subagent_meta(host_store, "host", "explore", 1)
            _emit_subagent_done(
                host_out,
                name="explore",
                prompt=500,
                completion=80,
                total=580,
                cached=30,
            )

            # Attached agent.
            session = Session(host_store, agent=host_stub, name="tree")
            helper = _stub_agent("helper")
            attach_agent_to_session(helper, session, role="reader")
            helper.output_router.notify_activity(
                "token_usage",
                "",
                metadata={
                    "prompt_tokens": 400,
                    "completion_tokens": 60,
                    "total_tokens": 460,
                    "cached_tokens": 5,
                },
            )

            loops = host_store.token_usage_all_loops()
            loops_map = dict(loops)
            paths = [path for path, _ in loops]

            assert "host" in loops_map
            assert "host:subagent:explore:0" in loops_map
            assert "host:attached:reader:0" in loops_map

            # Shape: own counters only.
            assert loops_map["host"]["prompt_tokens"] == 200
            assert loops_map["host"]["cached_tokens"] == 10
            assert loops_map["host:subagent:explore:0"]["prompt_tokens"] == 500
            assert loops_map["host:subagent:explore:0"]["cached_tokens"] == 30
            assert loops_map["host:attached:reader:0"]["prompt_tokens"] == 400
            assert loops_map["host:attached:reader:0"]["cached_tokens"] == 5

            # Sanity: no duplicate entries, list not dict.
            assert len(paths) == len(set(paths))
        finally:
            host_store.close(update_status=False)


class TestCachedTokensEndToEndThroughAllLoops:
    """Audit finding A: cached tokens flow parent → sub → read API."""

    def test_subagent_cached_tokens_visible_in_all_loops(self, store):
        """Regression fence — was the Wave A xfail, now a real test."""
        agent_stub = SimpleNamespace(controller=None, session=None)
        output = SessionOutput("parent", store, agent_stub)

        _seed_subagent_meta(store, "parent", "explore", 1)
        _emit_subagent_done(
            output,
            name="explore",
            prompt=500,
            completion=80,
            total=580,
            cached=123,
        )

        loops_map = dict(store.token_usage_all_loops())
        assert loops_map["parent:subagent:explore:0"]["cached_tokens"] == 123
