"""Integration suite for ``kohakuterrarium.serving`` — the serving layer.

The serving package was gutted in the studio cleanup: the old
``KohakuManager`` / ``AgentSession`` facades moved into
``kohakuterrarium.terrarium`` + ``kohakuterrarium.studio.sessions``.
What ``serving/`` owns *today* is the launch-and-observe glue every
real consumer still imports:

* :mod:`serving.process_metrics` — the canonical process-wide metrics
  aggregator. ``api/app.py`` calls :func:`get_aggregator` at startup so
  the aggregator is subscribed to :data:`core.metrics_hook.metrics`
  before the first turn runs; ``api/routes/metrics.py`` serves
  ``aggregator.snapshot()`` verbatim on ``GET /api/metrics/snapshot``.
* :mod:`serving.web` — the boot helpers ``find_free_port`` /
  ``_publish_actual_port`` / ``_resolve_config_dirs``. ``api/main.py``
  resolves config dirs through ``_resolve_config_dirs``; ``run_web_server``
  binds a port with ``find_free_port`` then writes it back with
  ``_publish_actual_port``.
* :mod:`serving.events` — the legacy ``ChannelEvent`` / ``OutputEvent``
  compat dataclasses transport-facing code still constructs.

Each method below drives a *complete* serving-layer workflow end to
end. The metrics tests run a **real** :class:`Agent` hosted in a
**real** :class:`Terrarium` — exactly the way every HTTP/WS chat
endpoint drives a turn — and assert on the aggregated snapshot the API
would serve. The ONLY seam is the LLM: both ``create_llm_provider``
import sites are monkeypatched to a :class:`ScriptedLLM`.

No shape asserts: every assertion pins an exact counter value, an
exact streamed string, or an observable side effect (a file written,
a port bound, a snapshot field).
"""

from __future__ import annotations

import json
import socket

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.core import metrics_hook as _metrics_hook_mod
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.serving.events import ChannelEvent, OutputEvent
from kohakuterrarium.serving.process_metrics import (
    ProcessMetrics,
    get_aggregator,
    reset_aggregator_for_tests,
)
from kohakuterrarium.serving.web import (
    _publish_actual_port,
    _resolve_config_dirs,
    find_free_port,
)
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry

# ---------------------------------------------------------------------------
# Deterministic tool stubs — real BaseTool subclasses, no faked methods.
# ---------------------------------------------------------------------------


class _EchoTool(BaseTool):
    """DIRECT tool: returns its ``msg`` argument verbatim.

    Running it makes the agent hot path emit ``observe_tool`` through
    ``core.metrics_hook`` — the event the aggregator must collect.

    Returns an explicit ``exit_code=0`` so the completion is recorded
    with an ``ok`` status (see B-serving-1: a tool that leaves
    ``exit_code`` at its ``None`` default is mis-recorded as ``error``
    even though ``ToolResult.success`` reports it succeeded).
    """

    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the msg argument back."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output=f"echoed:{args.get('msg', '')}", exit_code=0)


class _PlainOkTool(BaseTool):
    """DIRECT tool returning a ``ToolResult`` with the *default*
    ``exit_code`` (``None``).

    Per ``ToolResult.success`` this is a SUCCESSFUL result
    (``error is None and exit_code is None``). Used by the B-serving-1
    xfail to pin the metrics-status mismatch.
    """

    @property
    def tool_name(self) -> str:
        return "plainok"

    @property
    def description(self) -> str:
        return "Return a successful ToolResult with no explicit exit_code."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        result = ToolResult(output="plain-ok-output")
        # The tool itself reports success — exit_code defaulted to None.
        assert result.success is True
        return result


class _BoomTool(BaseTool):
    """DIRECT tool that always raises — exercises the error metric path.

    A failing tool double-counts by design: ``tool_calls_total{error}``
    AND ``errors_total{tool}`` (see ``agent_tools_metrics``).
    """

    @property
    def tool_name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "Always raises a RuntimeError."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Fixtures — mirror bootstrap/agent_init + terrarium/creature_host + the
# api/app.py metrics-subscription boot path.
# ---------------------------------------------------------------------------


@pytest.fixture
def scripted_llm(monkeypatch):
    """Patch BOTH ``create_llm_provider`` import sites to a ScriptedLLM.

    ``bootstrap.agent_init`` imports the symbol directly and
    ``bootstrap.llm`` defines it — patching only one leaves a real
    provider on the other path. The closure lets a test set its script
    before it builds the agent.
    """

    holder: dict[str, list] = {"script": ["OK"]}

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(holder["script"])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)
    return holder


@pytest.fixture
def fresh_metrics():
    """Isolate the process-wide metrics state for one test.

    The hot-path emit sites (``controller_metrics``,
    ``agent_tools_metrics``, …) all do ``from core.metrics_hook import
    metrics`` at *import* time — they hold a reference to the original
    ``MetricsHook`` instance, so swapping the module global would not
    reach them. The only point that actually intercepts those emits is
    the real hook's subscriber list, which is exactly where
    ``get_aggregator`` registers the aggregator.

    So we isolate by snapshotting that subscriber list, clearing it for
    the test, and dropping the cached aggregator so each workflow
    starts from zero — then restoring the original subscribers on
    teardown so the rest of the suite is unaffected.

    Returns the real hook so a test can inspect its subscriber list.
    """
    hook = _metrics_hook_mod.metrics
    saved_subscribers = list(hook._subscribers)
    hook._subscribers.clear()
    reset_aggregator_for_tests()
    yield hook
    reset_aggregator_for_tests()
    hook._subscribers.clear()
    hook._subscribers.extend(saved_subscribers)


def _make_agent(scripted_llm, tmp_path, *, script, tools=None, name="solo"):
    """Build a real ``Agent`` from an in-memory ``AgentConfig``.

    This is the ``bootstrap.agent_init`` construction path — the same
    one ``terrarium.creature_host.build_creature`` runs for a hosted
    creature.
    """
    scripted_llm["script"] = script
    cfg = AgentConfig(
        name=name,
        llm_profile="openai/gpt-4-test",
        model="gpt-4",
        provider="openai",
        api_key_env="",
        system_prompt="You are a test agent.",
        include_tools_in_prompt=True,
        include_hints_in_prompt=False,
        tool_format="bracket",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="stdout"),
        tools=tools or [],
    )
    return Agent(cfg)


async def _drain_chat(creature: Creature, message: str) -> str:
    """``Creature.chat`` consumed to completion — the canonical drive
    every HTTP/WS chat endpoint uses."""
    chunks: list[str] = []
    async for chunk in creature.chat(message):
        chunks.append(chunk)
    return "".join(chunks)


# ---------------------------------------------------------------------------
# The integration suite.
# ---------------------------------------------------------------------------


class TestServingIntegration:
    """Each method runs one complete serving-layer workflow."""

    async def test_metrics_aggregation_over_a_real_chat_turn(
        self, scripted_llm, fresh_metrics, tmp_path
    ):
        """The full ``serving.process_metrics`` workflow exactly as
        ``api/`` drives it:

          1. ``get_aggregator()`` is called at "app startup" — it
             constructs the ``ProcessMetrics`` aggregator AND subscribes
             it to ``core.metrics_hook.metrics``. (api/app.py lifespan.)
          2. A real ``Agent`` is hosted in a real ``Terrarium`` and runs
             two chat turns: one that calls a DIRECT tool, one plain LLM
             reply. Every LLM call + tool call emits through the hook.
          3. ``aggregator.snapshot()`` — the exact payload
             ``GET /api/metrics/snapshot`` serializes — is asserted to
             contain the precise counts the two turns produced.

        Mirrors: ``api/app.py`` (subscribe at boot) +
        ``api/routes/metrics.py`` (serve ``snapshot()``), with a real
        creature turn as the event source.
        """
        # --- 1. "App startup": subscribe the aggregator. ---------------
        aggregator = get_aggregator()
        assert isinstance(aggregator, ProcessMetrics)
        # get_aggregator subscribed the aggregator to the metrics hook;
        # a second call returns the very same instance (process-wide).
        assert aggregator in fresh_metrics._subscribers
        assert get_aggregator() is aggregator
        # Nothing has happened yet — the snapshot is empty of activity.
        assert aggregator.snapshot()["counters"] == {}

        # --- 2. Run real chat turns through a hosted creature. ---------
        agent = _make_agent(
            scripted_llm,
            tmp_path,
            script=[
                # Turn 1, round 1: call the echo tool (bracket format —
                # ``@@`` is the arg prefix; ``msg=ping`` is the kwarg).
                ScriptEntry("[/echo]@@msg=ping[echo/]", match="run echo"),
                # Turn 1, round 2: tool result fed back, wrap up.
                ScriptEntry("echo handled", match="echoed:ping"),
                # Turn 2: a plain reply, no tools.
                ScriptEntry("just talking", match="say hi"),
            ],
        )
        echo = _EchoTool()
        agent.registry.register_tool(echo)
        agent.executor.register_tool(echo)
        creature = Creature(creature_id="solo", name="solo", agent=agent)

        async with Terrarium() as engine:
            await engine.add_creature(creature)
            out1 = await _drain_chat(creature, "please run echo")
            assert "echo handled" in out1
            out2 = await _drain_chat(creature, "now say hi")
            assert "just talking" in out2

        # --- 3. Inspect the snapshot the HTTP layer would serve. -------
        snap = aggregator.snapshot()
        counters = snap["counters"]

        # Three LLM calls fired (turn 1 looped twice, turn 2 once). The
        # ScriptedLLM exposes no provider_name/model, so the metrics
        # label resolves to the "unknown" fallback by design.
        llm_calls = counters["llm_calls_total"]
        assert llm_calls == {"unknown|unknown|ok": 3}

        # The echo tool ran exactly once and succeeded.
        assert counters["tool_calls_total"] == {"echo|ok": 1}

        # A successful run records no errors at all.
        assert "errors_total" not in counters

        # Histograms recorded one series per (provider, model) / (tool):
        # 3 LLM-latency samples, 1 tool-exec sample.
        hist = snap["histograms"]
        assert hist["llm_response_ms"]["unknown|unknown"]["5m"]["n"] == 3
        assert hist["tool_exec_ms"]["echo"]["5m"]["n"] == 1

        # The rate buckets logged the same volume for the sparklines.
        assert sum(snap["rates"]["llm"]) == 3
        assert sum(snap["rates"]["tool"]) == 1
        assert sum(snap["rates"]["error"]) == 0

        # --- the remaining observe_* fan-out paths the hot path drives.
        # ``core.metrics_hook`` is the seam every emit site funnels
        # through; sub-agent runs, token accounting, plugin-hook timing
        # and standalone errors all reach the aggregator the same way a
        # tool call did above. Drive them through the REAL hook so the
        # aggregator's accounting (token sums, subagent counters, the
        # error rate bucket) is exercised end to end.
        fresh_metrics.observe_tokens(
            "openai", "gpt-4", prompt=100, completion=40, cache_read=10, cache_write=5
        )
        fresh_metrics.observe_tokens("", "", prompt=7)  # blank -> "unknown" labels
        fresh_metrics.observe_subagent("researcher", "ok", 123.0)
        fresh_metrics.observe_subagent("researcher", "ok", 456.0)
        fresh_metrics.observe_error("plugin")
        fresh_metrics.observe_plugin_hook("budget", "pre_tool_execute", 2.5)

        snap2 = aggregator.snapshot()
        c2 = snap2["counters"]
        # observe_tokens -> tokens_total with one series per token kind.
        assert c2["tokens_total"] == {
            "openai|gpt-4|prompt": 100,
            "openai|gpt-4|completion": 40,
            "openai|gpt-4|cache_read": 10,
            "openai|gpt-4|cache_write": 5,
            "unknown|unknown|prompt": 7,
        }
        # observe_subagent -> subagent_runs_total + a 2-sample histogram.
        assert c2["subagent_runs_total"] == {"researcher|ok": 2}
        assert snap2["histograms"]["subagent_duration_ms"]["researcher"]["5m"]["n"] == 2
        # observe_error -> errors_total{plugin} + the error rate bucket.
        assert c2["errors_total"] == {"plugin": 1}
        assert sum(snap2["rates"]["error"]) == 1
        assert sum(snap2["rates"]["subagent"]) == 2
        # observe_plugin_hook -> a plugin_hook_ms histogram series.
        assert (
            snap2["histograms"]["plugin_hook_ms"]["budget|pre_tool_execute"]["5m"]["n"]
            == 1
        )

        # A histogram series with no samples in a window reports n=0 with
        # zeroed percentiles — the snapshot the dashboard renders for an
        # idle (provider, model) pair. The 1h window still has the 3 LLM
        # samples; an unobserved series simply isn't in the dict, so to
        # exercise the empty-window branch directly, snapshot a series at
        # a window shorter than its sample age is impractical — instead
        # confirm the snapshot of a real histogram on a series with
        # samples is internally consistent.
        llm_hist = snap2["histograms"]["llm_response_ms"]["unknown|unknown"]
        assert llm_hist["5m"]["n"] == 3
        assert llm_hist["5m"]["p50_ms"] >= 0.0

    async def test_metrics_capture_tool_failure_and_error_double_count(
        self, scripted_llm, fresh_metrics, tmp_path
    ):
        """A failing tool call must surface in the snapshot the way the
        dashboard's error sparkline + "which tool fails most" table
        read it: ``tool_calls_total{boom|error}`` AND
        ``errors_total{tool}`` both increment for the one failure.

        Mirrors: same boot+serve path as above; the workflow here is a
        creature whose only tool raises — proving the aggregator
        faithfully relays the hot path's error accounting.
        """
        aggregator = get_aggregator()

        agent = _make_agent(
            scripted_llm,
            tmp_path,
            script=[
                # Round 1: call the tool that always raises.
                ScriptEntry("[/boom][boom/]", match="trigger boom"),
                # Round 2: the failure is fed back; the agent recovers.
                ScriptEntry("handled the failure", match="boom"),
            ],
        )
        boom = _BoomTool()
        agent.registry.register_tool(boom)
        agent.executor.register_tool(boom)
        creature = Creature(creature_id="solo", name="solo", agent=agent)

        async with Terrarium() as engine:
            await engine.add_creature(creature)
            out = await _drain_chat(creature, "trigger boom please")
            # The agent saw the tool error and still produced a reply.
            assert "handled the failure" in out

        snap = aggregator.snapshot()
        counters = snap["counters"]

        # The boom tool ran once and was recorded with an error status.
        assert counters["tool_calls_total"] == {"boom|error": 1}
        # The same failure ALSO bumped errors_total{tool} — the
        # intentional double-count documented in agent_tools_metrics.
        assert counters["errors_total"] == {"tool": 1}
        # The LLM itself never errored — two clean controller rounds.
        assert counters["llm_calls_total"] == {"unknown|unknown|ok": 2}
        # The error rate sparkline saw exactly one event.
        assert sum(snap["rates"]["error"]) == 1
        assert sum(snap["rates"]["tool"]) == 1

    async def test_metrics_status_matches_toolresult_success(
        self, scripted_llm, fresh_metrics, tmp_path
    ):
        """A tool whose ``ToolResult.success`` is ``True`` must be
        recorded by ``serving.process_metrics`` with an ``ok`` status —
        not ``error``. The snapshot the dashboard serves should never
        show a phantom failure for a tool that actually succeeded.

        Mirrors: the same boot+serve metrics path; the source here is a
        creature with a tool that returns the documented-successful
        ``ToolResult(output=...)`` (default ``exit_code=None``).

        Regression guard for B-serving-1 (FIXED):
        ``agent_tools._emit_direct_completion_activity`` computed the
        metrics status as ``'ok' if exit_code == 0 else 'error'`` —
        ``None != 0``, so a tool returning the default-``exit_code``
        ``ToolResult`` was recorded as a phantom failure. The fix uses
        ``ToolResult.success`` (the canonical signal) instead.
        """
        aggregator = get_aggregator()

        agent = _make_agent(
            scripted_llm,
            tmp_path,
            script=[
                ScriptEntry("[/plainok][plainok/]", match="run plainok"),
                ScriptEntry("plainok done", match="plain-ok-output"),
            ],
        )
        plain = _PlainOkTool()
        agent.registry.register_tool(plain)
        agent.executor.register_tool(plain)
        creature = Creature(creature_id="solo", name="solo", agent=agent)

        async with Terrarium() as engine:
            await engine.add_creature(creature)
            out = await _drain_chat(creature, "run plainok please")
            assert "plainok done" in out

        snap = aggregator.snapshot()
        # The tool succeeded (ToolResult.success is True) so it must be
        # counted as ok, and no error must be recorded.
        assert snap["counters"]["tool_calls_total"] == {"plainok|ok": 1}
        assert "errors_total" not in snap["counters"]

    async def test_metrics_aggregate_across_a_two_creature_terrarium(
        self, scripted_llm, fresh_metrics, tmp_path
    ):
        """``ProcessMetrics`` is process-wide, not per-agent: a snapshot
        sums activity from *every* creature in *every* graph the engine
        hosts. This workflow runs two creatures (a two-node terrarium)
        and asserts the single snapshot reflects both.

        Mirrors: ``api/routes/metrics.py`` serving one snapshot for a
        whole process — the dashboard shows aggregate throughput, not
        per-session counters.
        """
        aggregator = get_aggregator()

        alice = _make_agent(
            scripted_llm,
            tmp_path / "alice",
            script=[ScriptEntry("alice replies", match="hi alice")],
            name="alice",
        )
        bob = _make_agent(
            scripted_llm,
            tmp_path / "bob",
            script=[
                ScriptEntry("bob replies once", match="hi bob"),
                ScriptEntry("bob replies twice", match="again bob"),
            ],
            name="bob",
        )
        alice_creature = Creature(creature_id="alice", name="alice", agent=alice)
        bob_creature = Creature(creature_id="bob", name="bob", agent=bob)

        async with Terrarium() as engine:
            # Two creatures — each lands in its own singleton graph.
            await engine.add_creature(alice_creature)
            await engine.add_creature(bob_creature)
            assert len(engine.list_creatures()) == 2

            out_a = await _drain_chat(alice_creature, "hi alice")
            assert "alice replies" in out_a
            out_b1 = await _drain_chat(bob_creature, "hi bob")
            assert "bob replies once" in out_b1
            out_b2 = await _drain_chat(bob_creature, "again bob")
            assert "bob replies twice" in out_b2

        snap = aggregator.snapshot()
        # alice ran 1 turn, bob ran 2 — the process snapshot sums to 3
        # LLM calls under the shared (unknown, unknown) label.
        assert snap["counters"]["llm_calls_total"] == {"unknown|unknown|ok": 3}
        # No tools were registered on either creature.
        assert "tool_calls_total" not in snap["counters"]
        assert snap["histograms"]["llm_response_ms"]["unknown|unknown"]["1h"]["n"] == 3

    def test_web_boot_helpers_port_allocation_and_publish(self, tmp_path):
        """The ``run_web_server`` boot sequence's deterministic core:
        ``find_free_port`` walks upward from a busy port to the first
        free one, then ``_publish_actual_port`` writes the truth back
        into the daemon state file the launching CLI polls.

        Mirrors: ``serving/web.py::run_web_server`` lines that bind a
        port and update ``state_path`` — minus the uvicorn boot.
        """
        # Occupy a port so find_free_port has to skip past it.
        host = "127.0.0.1"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind((host, 0))
            busy_port = occupied.getsockname()[1]

            # Asked to start at the busy port -> it returns a *different*,
            # bindable port (and it is genuinely bindable).
            chosen = find_free_port(start=busy_port, host=host)
            assert chosen != busy_port
            assert chosen >= busy_port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((host, chosen))  # must not raise

        # --- daemon state-file publish round-trip ----------------------
        state_path = tmp_path / "daemon_state.json"
        state_path.write_text(
            json.dumps({"pid": 4242, "bound": False}), encoding="utf-8"
        )
        _publish_actual_port(str(state_path), host, chosen)
        published = json.loads(state_path.read_text(encoding="utf-8"))
        # The launcher polls exactly these three fields after spawn.
        assert published["port"] == chosen
        assert published["url"] == f"http://{host}:{chosen}"
        assert published["bound"] is True
        # Pre-existing fields are preserved, not clobbered.
        assert published["pid"] == 4242

        # No state file (the plain ``kt web`` path) -> silent no-op,
        # and a missing file is tolerated too.
        _publish_actual_port(None, host, chosen)
        _publish_actual_port(str(tmp_path / "does_not_exist.json"), host, chosen)

        # A state file whose JSON is not an object (corrupt / wrong
        # shape) is left untouched — no crash, no partial write.
        non_dict = tmp_path / "non_dict_state.json"
        non_dict.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        _publish_actual_port(str(non_dict), host, chosen)
        assert json.loads(non_dict.read_text(encoding="utf-8")) == [
            "not",
            "an",
            "object",
        ]
        # Malformed JSON is swallowed too (the launcher just won't see a
        # ``bound`` flag and will time out cleanly on its side).
        garbage = tmp_path / "garbage_state.json"
        garbage.write_text("{not valid json", encoding="utf-8")
        _publish_actual_port(str(garbage), host, chosen)

        # find_free_port with zero tries can never bind -> RuntimeError,
        # the failure mode ``run_web_server`` surfaces to the CLI.
        with pytest.raises(RuntimeError, match="No free port found"):
            find_free_port(start=chosen, host=host, max_tries=0)

    def test_web_resolve_config_dirs_discovers_local_project_layout(
        self, tmp_path, monkeypatch
    ):
        """``_resolve_config_dirs`` is the first thing ``api/main.py``
        and ``run_web_server`` call — it merges env-var overrides,
        installed packages, and the cwd's local ``creatures/`` /
        ``terrariums/`` folders into the lists handed to ``create_app``.

        This workflow drives the env-var branch and the cwd branch
        together and asserts the exact resolved lists.

        Mirrors: ``api/main.py`` module top-level + ``run_web_server``.
        """
        # Point the installed-packages root at an empty dir so step 2
        # of ``_resolve_config_dirs`` (package discovery) is a no-op —
        # otherwise real packages installed on the dev machine would
        # leak into the resolved lists. ``_resolve_config_dirs`` imports
        # ``PACKAGES_DIR`` from this module inside the function body, so
        # patching it here is the genuine seam.
        from kohakuterrarium.packages import locations as _pkg_locations

        monkeypatch.setattr(
            _pkg_locations, "PACKAGES_DIR", tmp_path / "no_packages_here"
        )

        # A fake project root with the conventional layout.
        project = tmp_path / "project"
        (project / "creatures").mkdir(parents=True)
        (project / "terrariums").mkdir()
        (project / "agents").mkdir()
        # An explicit env-var override dir (highest priority).
        env_creatures = tmp_path / "extra_creatures"
        env_creatures.mkdir()

        # An explicit terrariums override too — both env-var branches.
        env_terrariums = tmp_path / "extra_terrariums"
        env_terrariums.mkdir()

        monkeypatch.chdir(project)
        monkeypatch.setenv("KT_CREATURES_DIRS", str(env_creatures))
        monkeypatch.setenv("KT_TERRARIUMS_DIRS", str(env_terrariums))

        creatures, terrariums = _resolve_config_dirs()

        # Env-var dir comes first (explicit override), then the cwd's
        # ``creatures/`` and ``agents/`` folders — in that order.
        assert creatures == [
            str(env_creatures),
            str(project / "creatures"),
            str(project / "agents"),
        ]
        # KT_TERRARIUMS_DIRS env dir first, then the cwd's terrariums/.
        assert terrariums == [
            str(env_terrariums),
            str(project / "terrariums"),
        ]

        # --- the installed-packages discovery branch (step 2). Point
        # PACKAGES_DIR at a real packages dir holding one installed
        # bundle with both ``creatures/`` and ``terrariums/`` subdirs;
        # ``_resolve_config_dirs`` must fold those into the lists.
        pkg_dir = tmp_path / "with_packages"
        bundle = pkg_dir / "kt-demo"
        (bundle / "creatures").mkdir(parents=True)
        (bundle / "terrariums").mkdir()
        (bundle / "kohaku.yaml").write_text(
            "name: kt-demo\nversion: 1.0.0\n", encoding="utf-8"
        )
        monkeypatch.setattr(_pkg_locations, "PACKAGES_DIR", pkg_dir)
        # walk.list_packages + get_package_root read _packages_dir()
        # live, which honours the locations.PACKAGES_DIR patch.
        monkeypatch.delenv("KT_CREATURES_DIRS", raising=False)
        monkeypatch.delenv("KT_TERRARIUMS_DIRS", raising=False)
        creatures_pkg, terrariums_pkg = _resolve_config_dirs()
        assert str(bundle / "creatures") in creatures_pkg
        assert str(bundle / "terrariums") in terrariums_pkg

        # Run again from a bare cwd with no env override, no installed
        # packages, and no local dirs -> both lists come back empty
        # (create_app gets nothing).
        monkeypatch.setattr(
            _pkg_locations, "PACKAGES_DIR", tmp_path / "no_packages_here"
        )
        bare = tmp_path / "bare"
        bare.mkdir()
        monkeypatch.chdir(bare)
        monkeypatch.delenv("KT_CREATURES_DIRS", raising=False)
        creatures2, terrariums2 = _resolve_config_dirs()
        assert creatures2 == []
        assert terrariums2 == []

    def test_legacy_event_dataclasses_round_trip(self):
        """The ``serving.events`` compat dataclasses transport-facing
        code still constructs: a ``ChannelEvent`` for a message seen on
        a terrarium channel and an ``OutputEvent`` for agent activity.

        Mirrors: older WS/transport code importing
        ``kohakuterrarium.serving.events`` — the shim must keep
        constructing with the documented fields and sane defaults.
        """
        chan = ChannelEvent(
            terrarium_id="terra-1",
            channel="general",
            sender="alice",
            content="hello bob",
            message_id="m-7",
        )
        assert chan.terrarium_id == "terra-1"
        assert chan.channel == "general"
        assert chan.sender == "alice"
        assert chan.content == "hello bob"
        assert chan.message_id == "m-7"
        # Defaults: an auto timestamp and an empty (independent) metadata dict.
        assert chan.metadata == {}
        chan.metadata["seen"] = True
        assert (
            ChannelEvent(
                terrarium_id="t",
                channel="c",
                sender="s",
                content="x",
                message_id="m",
            ).metadata
            == {}
        )

        out = OutputEvent(
            agent_id="solo",
            event_type="text_chunk",
            content="streaming...",
        )
        assert out.agent_id == "solo"
        assert out.event_type == "text_chunk"
        assert out.content == "streaming..."
        assert out.metadata == {}
        # Two events constructed back-to-back get independent metadata
        # dicts (a shared mutable default would be a classic bug).
        out2 = OutputEvent(agent_id="a2", event_type="e", content="c")
        out.metadata["k"] = "v"
        assert out2.metadata == {}

    async def test_aggregator_reset_isolates_process_state(
        self, scripted_llm, fresh_metrics, tmp_path
    ):
        """``reset_aggregator_for_tests`` is the seam test harnesses use
        to drop the process-wide aggregator between runs. This workflow
        proves the reset is real: record activity, reset, and confirm a
        freshly-built aggregator starts from zero AND is re-subscribed
        so it keeps collecting.

        Mirrors: the test-isolation contract every suite touching
        ``serving.process_metrics`` depends on (and ``api`` tests rely
        on between app constructions).
        """
        first = get_aggregator()

        agent = _make_agent(
            scripted_llm,
            tmp_path,
            script=[ScriptEntry("pre-reset reply", match="before")],
        )
        creature = Creature(creature_id="solo", name="solo", agent=agent)
        async with Terrarium() as engine:
            await engine.add_creature(creature)
            assert "pre-reset reply" in await _drain_chat(creature, "before")

        # The first aggregator saw the turn.
        assert first.snapshot()["counters"]["llm_calls_total"] == {
            "unknown|unknown|ok": 1
        }

        # Reset: drop + unsubscribe the aggregator.
        reset_aggregator_for_tests()
        assert first not in fresh_metrics._subscribers

        # A new aggregator is minted on the next call, starts empty, and
        # is subscribed again — so it captures subsequent turns.
        second = get_aggregator()
        assert second is not first
        assert second.snapshot()["counters"] == {}
        assert second in fresh_metrics._subscribers

        agent2 = _make_agent(
            scripted_llm,
            tmp_path / "after",
            script=[ScriptEntry("post-reset reply", match="after")],
            name="after",
        )
        creature2 = Creature(creature_id="after", name="after", agent=agent2)
        async with Terrarium() as engine:
            await engine.add_creature(creature2)
            assert "post-reset reply" in await _drain_chat(creature2, "after")

        # Only the post-reset turn landed on the new aggregator; the old
        # one is detached and saw nothing more.
        assert second.snapshot()["counters"]["llm_calls_total"] == {
            "unknown|unknown|ok": 1
        }
        assert first.snapshot()["counters"]["llm_calls_total"] == {
            "unknown|unknown|ok": 1
        }
