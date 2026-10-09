"""Unit tests for :mod:`kohakuterrarium.studio.sessions.summary.tracking`."""

from types import SimpleNamespace

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.identity.session_summary import SummarySettings
from kohakuterrarium.studio.sessions.lifecycle import start_creature
from kohakuterrarium.studio.sessions.summary import tracking
from kohakuterrarium.studio.sessions.summary.record import read_summary
from kohakuterrarium.studio.sessions.summary.sources import LLM_MARKER
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.testing.llm import ScriptEntry


async def _chat(service, cid, text):
    async for _ in service.chat(cid, text):
        pass


async def test_a_started_creature_session_is_summarized_after_its_turn(
    tmp_path, scripted, creature_dir
):
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        session = await start_creature(service, config_path=str(creature_dir))
        store = engine._session_stores[session.session_id]
        hook = tracking.hook_of(store)
        assert hook is not None
        tracking.track(engine)
        assert tracking.hook_of(store) is hook
        await _chat(
            service, session.creatures[0]["creature_id"], "Draft the release notes"
        )
        await hook.idle()
        summary = read_summary(store)
        assert (summary["text"], summary["source"], summary["at_turn"]) == (
            "Draft the release notes",
            "heuristic",
            1,
        )
    finally:
        await engine.shutdown()


async def test_llm_source_calls_a_separate_provider_with_the_summary_prompt(
    tmp_path, scripted, creature_dir, monkeypatch
):
    monkeypatch.setenv("KT_SESSION_SUMMARY_SOURCE", "llm")
    scripted["script"] = [
        ScriptEntry("Release notes for 2.1", match=LLM_MARKER),
        "Here are the notes.",
    ]
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    service = LocalTerrariumService(engine)
    try:
        session = await start_creature(service, config_path=str(creature_dir))
        store = engine._session_stores[session.session_id]
        creature = engine.get_creature(session.creatures[0]["creature_id"])
        await _chat(service, creature.creature_id, "Draft the release notes")
        hook = tracking.hook_of(store)
        await hook.idle()
        assert read_summary(store)["text"] == "Release notes for 2.1"
        first = hook.llm_for("probe", SummarySettings())
        assert hook.llm_for("probe", SummarySettings()) is first
        assert hook.llm_for("probe", SummarySettings(model="p/other")) is not first
        summary_calls = [
            llm
            for llm in scripted["built"]
            if any(
                LLM_MARKER in str(m.get("content")) for log in llm.call_log for m in log
            )
        ]
        assert summary_calls and creature.agent.llm not in summary_calls
    finally:
        await engine.shutdown()


def test_creature_agent_and_summary_llm_resolution(tmp_path, scripted):
    store = SessionStore(str(tmp_path / "s.kohakutr"))
    agent = SimpleNamespace(llm="active", _build_compact_llm=lambda cfg: "isolated")
    graph = SimpleNamespace(creature_ids=["c1", "c2"])
    creatures = {
        "c1": SimpleNamespace(name="other", agent=None),
        "c2": SimpleNamespace(name="main", agent=agent),
    }
    engine = SimpleNamespace(
        _session_stores={"g": store},
        get_graph=lambda gid: graph,
        get_creature=lambda cid: creatures[cid],
    )
    try:
        assert tracking.creature_agent(engine, store, "main") is agent
        assert tracking.creature_agent(engine, store, "missing") is None
        assert tracking.creature_agent(engine, object(), "main") is None
        assert tracking.summary_llm(agent, SummarySettings()) == "isolated"
        assert (
            tracking.summary_llm(SimpleNamespace(llm="active"), SummarySettings())
            == "active"
        )
        assert tracking.summary_llm(None, SummarySettings()) is None
        tracking.summary_llm(None, SummarySettings(model="profile/x"))
        assert len(scripted["built"]) == 1
    finally:
        store.close(update_status=False)


async def test_attach_skips_read_only_and_missing_stores(tmp_path):
    SessionStore(str(tmp_path / "r.kohakutr")).close(update_status=False)
    readonly = SessionStore.open_readonly(tmp_path / "r.kohakutr")
    try:
        assert tracking.attach(None, readonly) is None
        assert tracking.attach(None, None) is None
    finally:
        readonly.close(update_status=False)
    tracking.track(None)
