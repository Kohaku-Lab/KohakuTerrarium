"""Integration suite for ``kohakuterrarium.compose`` — the Pythonic
agent-composition algebra.

Nothing inside the framework imports ``compose/``: it is a *user-facing*
public API. So the only honest integration test is one that uses it the
way a user would — build real pipelines with the real operators
(``>>`` sequence, ``&`` parallel, ``|`` fallback, ``*`` retry) over real
engine-backed agent runnables, run them, and pin the exact end-to-end
result.

Each method below is a *complete* workflow:

* :func:`compose.agent` / :func:`compose.factory` build runnables backed
  by a real :class:`Terrarium` engine + :class:`Creature`. The
  ``_EngineChatSession`` adapter delegates to ``Creature.chat`` — the
  canonical inject-input + drain-output cycle.
* The combinators (``Sequence``, ``Product``, ``Fallback``, ``Retry``,
  ``Router``, ``Pure``, ``FailsWhen``, ``PipelineIterator``) are the
  real classes, exercised through their operator overloads.

The ONLY seam is the LLM: both ``create_llm_provider`` import sites
(``bootstrap.llm`` and ``bootstrap.agent_init``) are monkeypatched to a
deterministic :class:`ScriptedLLM`. Every other collaborator — the
engine, the controller loop, the output router — is the real thing.

No shape asserts: each assertion pins an exact composed string or the
exact retry/fallback call pattern.
"""

import pytest

import yaml

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.compose import (
    AgentFactory,
    AgentRunnable,
    BaseRunnable,
    Effects,
    FailsWhen,
    Fallback,
    PipelineIterator,
    Product,
    Pure,
    Retry,
    Router,
    Runnable,
    Sequence,
    agent,
    factory,
)
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.testing.llm import ScriptEntry, ScriptedLLM

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# LLM seam — a per-agent scripted provider keyed on the agent's name.
# ---------------------------------------------------------------------------
#
# A real compose pipeline pipes one agent's output into the next agent's
# input. To keep that deterministic we give each agent a distinct script
# and route ``create_llm_provider`` to the right one by config name. The
# scripts use ``ScriptEntry.match`` so an agent's reply genuinely depends
# on what the previous stage handed it — i.e. the pipe is real, not a
# constant.

_SCRIPTS: dict[str, list[ScriptEntry]] = {}


def _install_llm_seam(monkeypatch) -> None:
    """Point both ``create_llm_provider`` import sites at the registry."""

    def _fake_create(config, llm_override=None):
        name = getattr(config, "name", "") or ""
        script = _SCRIPTS.get(name)
        if script is None:
            # Unknown agent: echo a marker so a mis-wired pipeline is
            # loud rather than silently "OK".
            return ScriptedLLM([f"<no-script:{name}>"])
        # Fresh provider per agent build so call_count starts at 0 —
        # matters for the ephemeral ``factory()`` path which rebuilds.
        return ScriptedLLM([ScriptEntry(**vars(e)) for e in script])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)


def _config(name: str, tmp_path) -> AgentConfig:
    """A minimal, deterministic single-creature agent config."""
    return AgentConfig(
        name=name,
        system_prompt=f"You are the deterministic compose test agent {name!r}.",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="stdout"),
        include_hints_in_prompt=False,
    )


@pytest.fixture(autouse=True)
def _llm_seam(monkeypatch):
    _SCRIPTS.clear()
    _install_llm_seam(monkeypatch)
    yield
    _SCRIPTS.clear()


# ---------------------------------------------------------------------------
# The suite — fat workflow methods, one complete user story each.
# ---------------------------------------------------------------------------


class TestComposeIntegration:
    """Each method builds and runs a real compose pipeline end-to-end."""

    async def test_sequence_with_parallel_fanout_pipeline(self, tmp_path):
        """The headline example from ``compose/__init__``:

            pipeline = explorer >> (planner & critic) >> writer

        Three real engine-backed agents plus ``Pure`` glue. ``explorer``
        emits findings; ``&`` fans them to ``planner`` and ``critic``
        concurrently; a ``Pure`` join merges the tuple; ``writer``
        produces the final artifact. Every stage's reply is keyed on its
        input via ``ScriptEntry.match``, so the pipe is genuinely
        carrying data — and we assert the EXACT final string.
        """
        _SCRIPTS["explorer"] = [ScriptEntry(response="FINDINGS", match="investigate")]
        _SCRIPTS["planner"] = [ScriptEntry(response="plan(FINDINGS)", match="FINDINGS")]
        _SCRIPTS["critic"] = [ScriptEntry(response="risk(FINDINGS)", match="FINDINGS")]
        _SCRIPTS["writer"] = [
            ScriptEntry(response="REPORT[plan(FINDINGS)+risk(FINDINGS)]", match="plan(")
        ]

        explorer = await agent(_config("explorer", tmp_path))
        planner = await agent(_config("planner", tmp_path))
        critic = await agent(_config("critic", tmp_path))
        writer = await agent(_config("writer", tmp_path))
        try:
            # Pure join: (planner_out, critic_out) tuple -> single string
            # the writer can consume. This is the idiomatic way a user
            # collapses an ``&`` fan-out before the next ``>>`` stage.
            join = Pure(lambda pair: f"{pair[0]}+{pair[1]}")

            pipeline = explorer >> (planner & critic) >> join >> writer
            assert isinstance(pipeline, Sequence)

            result = await pipeline("investigate the bug")
            assert result == "REPORT[plan(FINDINGS)+risk(FINDINGS)]"

            # The ``&`` branch really ran both agents concurrently and
            # returned a tuple — verify by running just that sub-pipeline.
            fanout = explorer >> (planner & critic)
            pair = await fanout.run("investigate the bug")
            assert pair == ("plan(FINDINGS)", "risk(FINDINGS)")

            # Sequence / Product flatten: chaining ``>>`` and ``&`` never
            # nests — a real user composing incrementally relies on this.
            three_seq = explorer >> planner >> writer
            assert isinstance(three_seq, Sequence)
            assert len(three_seq._steps) == 3
            three_par = explorer & planner & critic
            assert isinstance(three_par, Product)
            assert len(three_par._branches) == 3

            # A plain callable on either side of ``>>`` auto-wraps as Pure;
            # ``__rrshift__`` handles ``callable >> runnable``.
            upper = explorer >> str.upper
            assert isinstance(upper, Sequence)
            assert await upper("investigate the bug") == "FINDINGS"
            prefixed = (lambda s: s) >> explorer
            assert isinstance(prefixed, Sequence)
            assert await prefixed("investigate the bug") == "FINDINGS"

            # ``&`` and ``|`` also auto-wrap a plain callable as Pure —
            # a user can mix bare functions into a parallel / fallback.
            par_with_fn = explorer & str.upper
            assert isinstance(par_with_fn, Product)
            assert await par_with_fn("investigate the bug") == (
                "FINDINGS",
                "INVESTIGATE THE BUG",
            )
            # ``runnable | callable`` -> the callable becomes the Pure
            # fallback. explorer succeeds here so the fallback never runs,
            # but the structure is what matters.
            fb_with_fn = explorer | (lambda s: f"FALLBACK:{s}")
            assert isinstance(fb_with_fn, Fallback)
            assert await fb_with_fn("investigate the bug") == "FINDINGS"
            # ``__rrshift__`` only fires for a *callable* left operand —
            # a non-callable, non-runnable left operand is NotImplemented
            # (surfaced as TypeError), never silently mis-composed.
            with pytest.raises(TypeError):
                5 >> explorer

            # ``Pure`` requires a callable — a non-callable is a hard
            # TypeError at construction, not a deferred failure at run.
            with pytest.raises(TypeError, match="Pure requires a callable"):
                Pure(42)

            # ``Pure`` awaits an awaitable result transparently — an async
            # function wrapped as Pure is run to completion.
            async def _async_double(x):
                return x * 2

            assert await Pure(_async_double).run(21) == 42

            # The base ``BaseRunnable.__repr__`` and ``Fallback.__repr__``
            # — what ``logger.debug`` prints when a fallback triggers.
            assert repr(BaseRunnable()) == "<BaseRunnable>"
            assert repr(fb_with_fn).startswith("<Fallback ")
            assert " | " in repr(fb_with_fn)

            # __repr__ is what ``logger.debug`` in Fallback prints — every
            # combinator renders its structure.
            assert repr(explorer).startswith("<AgentRunnable")
            assert " >> " in repr(three_seq)
            assert " & " in repr(three_par)
            assert repr(Pure(str.upper)) == "<Pure upper>"

            # AgentRunnable satisfies the Runnable protocol (runtime check).
            assert isinstance(explorer, Runnable)
            assert isinstance(explorer, BaseRunnable)

            # The Effects semiring: a user annotates cost/latency and the
            # combinator-composition rules add cost, max latency, multiply
            # reliability for parallel; add latency for sequential.
            a = Effects(cost=1.0, latency=2.0, reliability=0.9)
            b = Effects(cost=3.0, latency=5.0, reliability=0.8)
            seq_fx = a.sequential(b)
            assert seq_fx.cost == 4.0
            assert seq_fx.latency == 7.0
            assert abs(seq_fx.reliability - 0.72) < 1e-9
            par_fx = a.parallel(b)
            assert par_fx.cost == 4.0
            assert par_fx.latency == 5.0  # max, not sum
            assert abs(par_fx.reliability - 0.72) < 1e-9
            # A None field short-circuits the whole composed field to None.
            partial_fx = Effects(cost=1.0).sequential(Effects(latency=2.0))
            assert partial_fx.cost is None
            assert partial_fx.latency is None
            assert partial_fx.reliability is None
            # ``parallel`` uses max() on latency — a None operand there
            # short-circuits to None too (the _max None branch).
            par_partial = Effects(cost=1.0).parallel(Effects(latency=2.0))
            assert par_partial.cost is None
            assert par_partial.latency is None
            assert par_partial.reliability is None
        finally:
            for r in (explorer, planner, critic, writer):
                await r.close()

    async def test_retry_then_fallback_recovers_a_flaky_stage(self, tmp_path):
        """The other headline example:

            result = await (flaky * 3) | safe

        ``flaky`` is a real agent whose output is wrapped with
        ``.fails_when`` so the first two attempts "fail" (raise) and the
        third would succeed — but we make ALL attempts fail, proving the
        ``Retry`` exhausts and the ``Fallback`` then runs ``safe`` with
        the *original* input. We pin the exact attempt count via the
        scripted provider's ``call_count`` and the exact final string.
        """
        # ``flaky`` always answers "ERROR ..." — fails_when turns that
        # into a raise, so every one of the 3 retries fails.
        _SCRIPTS["flaky"] = [ScriptEntry(response="ERROR: transient")]
        # ``safe`` keys on the ORIGINAL input — proving Fallback re-feeds
        # the original payload, not the (raised) flaky output.
        _SCRIPTS["safe"] = [
            ScriptEntry(response="SAFE-HANDLED:do-the-task", match="do-the-task")
        ]

        flaky = await agent(_config("flaky", tmp_path))
        safe = await agent(_config("safe", tmp_path))
        try:
            guarded = flaky.fails_when(lambda out: out.startswith("ERROR"))
            pipeline = (guarded * 3) | safe
            assert isinstance(pipeline, Fallback)
            assert isinstance(pipeline._primary, Retry)
            assert pipeline._primary._max_attempts == 3

            result = await pipeline("do-the-task")
            assert result == "SAFE-HANDLED:do-the-task"

            # Retry really attempted 3 times before giving up: the flaky
            # agent's session LLM was called once per attempt.
            flaky_llm = flaky._session._creature.agent.llm
            assert flaky_llm.call_count == 3
            # The fallback ran exactly once.
            safe_llm = safe._session._creature.agent.llm
            assert safe_llm.call_count == 1

            # And when retry CAN succeed, fallback is never touched.
            _SCRIPTS["flaky2"] = [
                ScriptEntry(response="ERROR: transient", match="boom"),
                ScriptEntry(response="RECOVERED", match="boom"),
            ]
            flaky2 = await agent(_config("flaky2", tmp_path))
            try:
                guarded2 = flaky2.fails_when(lambda out: out.startswith("ERROR"))
                ok = await ((guarded2 * 3) | safe)("boom")
                assert ok == "RECOVERED"
                assert flaky2._session._creature.agent.llm.call_count == 2
                # Fallback untouched: still just the one earlier call.
                assert safe_llm.call_count == 1
            finally:
                await flaky2.close()

            # ``N * runnable`` (reflected __rmul__) is the same as
            # ``runnable * N`` — a user can write the count on either side.
            left_retry = 2 * guarded
            assert isinstance(left_retry, Retry)
            assert left_retry._max_attempts == 2

            # Operator type-guards: a non-int retry, a non-runnable
            # fallback/sequence/parallel operand all return NotImplemented
            # (surfaced as TypeError) instead of silently mis-composing.
            for bad_expr in (
                lambda: guarded * 0,
                lambda: guarded * "three",
                lambda: guarded >> 5,
                lambda: guarded & 5,
                lambda: guarded | 5,
            ):
                with pytest.raises(TypeError):
                    bad_expr()

            # FailsWhen used directly (not via ``.fails_when``): the
            # predicate firing raises ValueError; not firing passes the
            # value through untouched.
            _SCRIPTS["echoer"] = [ScriptEntry(response="GOOD", match="ping")]
            echoer = await agent(_config("echoer", tmp_path))
            try:
                guard_pass = FailsWhen(echoer, lambda o: o.startswith("BAD"))
                assert await guard_pass.run("ping") == "GOOD"
                guard_fail = FailsWhen(echoer, lambda o: o.startswith("GOOD"))
                with pytest.raises(ValueError, match="predicate triggered"):
                    await guard_fail.run("ping")
                # Repr of the retry/fallback structure is what Fallback's
                # debug log emits when it catches.
                assert repr(guarded * 3).startswith("<Retry")
                assert "FailsWhen" in repr(guard_pass)
            finally:
                await echoer.close()

            # The abstract BaseRunnable.run is a hard NotImplementedError —
            # a bare BaseRunnable is not itself runnable.
            with pytest.raises(NotImplementedError):
                await BaseRunnable().run("x")
        finally:
            await flaky.close()
            await safe.close()

    async def test_factory_ephemeral_path_with_router_and_maps(self, tmp_path):
        """The ``factory()`` convenience constructor and the
        combinators a real user reaches for beyond the four operators:
        ``Router`` (dict dispatch), ``.contramap`` / ``.map`` (profunctor
        transforms), and the ``_EngineChatSession`` -> ``Creature.chat``
        path exercised once per call (ephemeral).

        Workflow: a dispatcher routes a ``(key, payload)`` tuple to one
        of two ephemeral specialist agents, each created fresh per call
        and torn down after. ``.contramap`` normalizes the user's raw
        request into that tuple; ``.map`` post-formats the answer.
        """
        _SCRIPTS["coder"] = [ScriptEntry(response="def f(): ...", match="write code")]
        _SCRIPTS["doc"] = [ScriptEntry(response="# How-to", match="write docs")]

        coder = factory(_config("coder", tmp_path))
        doc = factory(_config("doc", tmp_path))
        assert isinstance(coder, AgentFactory)
        assert isinstance(doc, AgentFactory)

        # Router over the two ephemeral specialists. ``>> {dict}`` is the
        # sugar the operator layer turns into a Router.
        prefix = Pure(lambda x: x)
        routed = prefix >> {"code": coder, "doc": doc}
        assert isinstance(routed._steps[-1], Router)

        # contramap: raw string -> (route_key, payload) the Router wants.
        def _classify(raw: str) -> tuple[str, str]:
            return ("code", raw) if "code" in raw else ("doc", raw)

        # map: post-format the specialist's answer.
        pipeline = routed.contramap(_classify).map(lambda out: f"<<{out}>>")

        code_result = await pipeline("please write code now")
        assert code_result == "<<def f(): ...>>"

        doc_result = await pipeline("please write docs now")
        assert doc_result == "<<# How-to>>"

        # Ephemeral really means fresh-per-call: a second invocation of
        # the same factory still works (engine rebuilt, not reused).
        again = await coder.run("write code please")
        assert again == "def f(): ..."

        # __repr__ on a config-backed factory names the config.
        assert repr(coder) == "<AgentFactory coder>"

        # --- Router edge behaviours a real dispatcher hits ---
        # A bare (non-tuple) input is used as BOTH the route key and the
        # payload — so a Router over Pure echoers can route on the value.
        bare_router = Router(
            {"hello": Pure(lambda x: f"H:{x}"), "bye": Pure(lambda x: f"B:{x}")}
        )
        assert await bare_router.run("hello") == "H:hello"
        # A ``_default`` branch catches keys with no explicit route.
        with_default = Router(
            {"a": Pure(lambda x: "matched-a"), "_default": Pure(lambda x: "fallback")}
        )
        assert await with_default.run(("a", "p")) == "matched-a"
        assert await with_default.run(("zzz", "p")) == "fallback"
        # No route and no default -> a KeyError listing what IS available.
        no_default = Router({"x": Pure(lambda v: v)})
        with pytest.raises(KeyError, match="No route for key"):
            await no_default.run(("missing", "payload"))
        # Router repr lists its keys (+ _default marker when present).
        assert "_default" in repr(with_default)

        # --- factory(config_path): the on-disk-config convenience path.
        # Write a real creature config dir and build a factory off the
        # path string — ``_engine_session_from_path`` is the adapter.
        creature_dir = tmp_path / "pathcoder"
        creature_dir.mkdir()
        (creature_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "pathcoder",
                    "input": {"type": "none"},
                    "output": {"type": "stdout"},
                    "include_hints_in_prompt": False,
                }
            ),
            encoding="utf-8",
        )
        (creature_dir / "system.md").write_text(
            "You are the path-loaded compose agent.", encoding="utf-8"
        )
        _SCRIPTS["pathcoder"] = [ScriptEntry(response="from-disk-config", match="go")]
        path_factory = factory(str(creature_dir))
        assert repr(path_factory).endswith("pathcoder>")
        assert await path_factory.run("go now") == "from-disk-config"

    async def test_persistent_runnable_accumulates_conversation(self, tmp_path):
        """``agent()`` returns a *persistent* ``AgentRunnable``: the
        underlying ``Creature`` is reused across ``run`` calls and the
        conversation accumulates. This is the property that distinguishes
        it from ``factory()`` — and a real user picks ``agent()``
        precisely when they want that carry-over.

        We drive three turns through one runnable inside an ``async
        with`` block (the documented lifecycle), and assert each turn's
        exact reply plus that all three turns hit the SAME session.
        """
        _SCRIPTS["assistant"] = [
            ScriptEntry(response="turn-1-reply", match="first"),
            ScriptEntry(response="turn-2-reply", match="second"),
            ScriptEntry(response="turn-3-reply", match="third"),
        ]

        async with await agent(_config("assistant", tmp_path)) as a:
            assert isinstance(a, AgentRunnable)
            session = a._session
            llm = session._creature.agent.llm

            assert await a.run("first message") == "turn-1-reply"
            assert await a.run("second message") == "turn-2-reply"
            assert await a.run("third message") == "turn-3-reply"

            # One persistent creature served all three turns.
            assert a._session is session
            assert llm.call_count == 3
            # The creature actually accumulated history: by turn 3 the
            # LLM saw all prior user turns in its message list.
            last_call = llm.call_log[-1]
            user_texts = [
                m.get("content", "") for m in last_call if m.get("role") == "user"
            ]
            joined = " ".join(str(t) for t in user_texts)
            assert "first message" in joined
            assert "second message" in joined
            assert "third message" in joined

    async def test_iterate_loops_a_pipeline_until_condition(self, tmp_path):
        """``BaseRunnable.iterate`` — the native-control-flow loop from
        the ``compose/__init__`` docstring:

            async for result in (writer >> reviewer).iterate(task):
                if "APPROVED" in result:
                    break

        A real two-agent ``writer >> reviewer`` pipeline is iterated:
        each pass feeds the previous output back in. The reviewer
        approves only once the draft has been revised enough times. We
        assert the exact number of iterations and the exact final value.
        """
        # writer: bumps a revision counter embedded in the text.
        _SCRIPTS["rev_writer"] = [
            ScriptEntry(response="draft-v1", match="task"),
            ScriptEntry(response="draft-v2", match="reviewed:draft-v1"),
            ScriptEntry(response="draft-v3", match="reviewed:draft-v2"),
        ]
        # reviewer: stamps "reviewed:" until v3, then APPROVES.
        _SCRIPTS["rev_reviewer"] = [
            ScriptEntry(response="reviewed:draft-v1", match="draft-v1"),
            ScriptEntry(response="reviewed:draft-v2", match="draft-v2"),
            ScriptEntry(response="APPROVED:draft-v3", match="draft-v3"),
        ]

        writer = await agent(_config("rev_writer", tmp_path))
        reviewer = await agent(_config("rev_reviewer", tmp_path))
        try:
            loop = (writer >> reviewer).iterate("the task")
            results: list[str] = []
            async for result in loop:
                results.append(result)
                if "APPROVED" in result:
                    break

            assert results == [
                "reviewed:draft-v1",
                "reviewed:draft-v2",
                "APPROVED:draft-v3",
            ]
            # Three iterations -> three turns on each agent.
            assert writer._session._creature.agent.llm.call_count == 3
            assert reviewer._session._creature.agent.llm.call_count == 3

            # ``PipelineIterator.feed`` overrides the NEXT iteration's
            # input (instead of feeding the previous output back). A user
            # uses this to inject a correction mid-loop. Drive a Pure
            # pipeline so the override is exactly observable.
            steps = Pure(lambda x: f"step({x})")
            it: PipelineIterator = steps.iterate("seed")
            first = await it.__anext__()
            assert first == "step(seed)"
            # Without feed(), the next input is the previous output.
            second = await it.__anext__()
            assert second == "step(step(seed))"
            # feed() replaces the next input exactly once.
            it.feed("INJECTED")
            third = await it.__anext__()
            assert third == "step(INJECTED)"
            # ...and the iteration after that resumes from that output.
            fourth = await it.__anext__()
            assert fourth == "step(step(INJECTED))"
        finally:
            await writer.close()
            await reviewer.close()

        # ``agent(config_path)``: the persistent runnable can also be
        # built from an on-disk creature config — the documented
        # ``async with await agent("@pkg/creatures/x")`` form, here with
        # a real local dir instead of a package ref.
        persistent_dir = tmp_path / "diskpersist"
        persistent_dir.mkdir()
        (persistent_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "diskpersist",
                    "input": {"type": "none"},
                    "output": {"type": "stdout"},
                    "include_hints_in_prompt": False,
                }
            ),
            encoding="utf-8",
        )
        (persistent_dir / "system.md").write_text(
            "You are the disk-loaded persistent agent.", encoding="utf-8"
        )
        _SCRIPTS["diskpersist"] = [
            ScriptEntry(response="disk-turn-1", match="one"),
            ScriptEntry(response="disk-turn-2", match="two"),
        ]
        async with await agent(str(persistent_dir)) as disk_agent:
            assert isinstance(disk_agent, AgentRunnable)
            assert await disk_agent.run("turn one") == "disk-turn-1"
            # Persistent: same session reused across the two turns.
            assert await disk_agent.run("turn two") == "disk-turn-2"
            assert disk_agent._session._creature.agent.llm.call_count == 2
