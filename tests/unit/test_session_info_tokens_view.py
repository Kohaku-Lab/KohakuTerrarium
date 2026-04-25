"""Wave G — :meth:`Agent.session_info` ``tokens_view`` parameter.

Covers the thin :func:`build_session_info` helper in
``core/agent_observability.py`` that :meth:`Agent.session_info`
delegates to. Uses a duck-typed agent stub so we don't pay the full
``Agent.__init__`` price; the contract under test is purely about the
shape of the returned payload for the two supported ``tokens_view``
values.
"""

from types import SimpleNamespace

from kohakuterrarium.core.agent_observability import build_session_info
from kohakuterrarium.session.output import SessionOutput
from kohakuterrarium.session.store import SessionStore


def _stub_agent(name, store):
    return SimpleNamespace(
        config=SimpleNamespace(name=name),
        session_store=store,
    )


def _seed_parent_tokens(store, agent_name, prompt, completion, cached):
    agent_stub = SimpleNamespace(controller=None, session=None)
    output = SessionOutput(agent_name, store, agent_stub)
    output.on_activity_with_metadata(
        "token_usage",
        "",
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "cached_tokens": cached,
        },
    )


def _seed_subagent(store, parent, name, prompt, completion, cached):
    agent_stub = SimpleNamespace(controller=None, session=None)
    output = SessionOutput(parent, store, agent_stub)
    run = store.next_subagent_run(parent, name)
    store.save_subagent(parent=parent, name=name, run=run, meta={"task": "stub"})
    output.on_activity_with_metadata(
        "subagent_done",
        f"[{name}] done",
        {
            "job_id": f"{name}_{run}",
            "result": "ok",
            "turns": 1,
            "tools_used": [],
            "duration": 0.1,
            "total_tokens": prompt + completion,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cached_tokens": cached,
        },
    )


def test_tokens_view_own_matches_store_token_usage(tmp_path):
    store = SessionStore(tmp_path / "info_own.kohakutr.v2")
    store.init_meta(
        session_id="info_own",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["host"],
    )
    try:
        _seed_parent_tokens(store, "host", prompt=120, completion=30, cached=15)
        agent = _stub_agent("host", store)

        info = build_session_info(agent, "own")
        assert info["agent"] == "host"
        # Own view = the dict returned by store.token_usage(agent).
        assert info["tokens"] == store.token_usage("host")
        assert info["tokens"]["prompt_tokens"] == 120
        assert info["tokens"]["cached_tokens"] == 15
    finally:
        store.close(update_status=False)


def test_tokens_view_all_loops_matches_store_enumeration(tmp_path):
    store = SessionStore(tmp_path / "info_all.kohakutr.v2")
    store.init_meta(
        session_id="info_all",
        config_type="agent",
        config_path="/tmp",
        pwd=str(tmp_path),
        agents=["host"],
    )
    try:
        _seed_parent_tokens(store, "host", prompt=120, completion=30, cached=0)
        _seed_subagent(store, "host", "explore", prompt=500, completion=80, cached=40)
        agent = _stub_agent("host", store)

        info = build_session_info(agent, "all_loops")
        assert info["agent"] == "host"
        # Shape: list of (path, usage) tuples — same as store API.
        assert info["tokens"] == store.token_usage_all_loops()
        paths = [p for p, _ in info["tokens"]]
        assert "host" in paths
        assert "host:subagent:explore:0" in paths
    finally:
        store.close(update_status=False)


def test_tokens_view_without_store_returns_empty_shape():
    # No session_store attached → empty dict for "own", empty list for
    # "all_loops" (so callers can rely on iterable / dict semantics).
    agent = _stub_agent("host", store=None)
    own = build_session_info(agent, "own")
    assert own["tokens"] == {}
    all_loops = build_session_info(agent, "all_loops")
    assert all_loops["tokens"] == []
