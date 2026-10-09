"""Unit tests for :mod:`kohakuterrarium.studio.sessions.summary.hook`."""

import asyncio

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.identity.session_summary import SummarySettings
from kohakuterrarium.studio.sessions.summary import hook as hook_mod
from kohakuterrarium.studio.sessions.summary.hook import SummaryHook, event_agent
from kohakuterrarium.studio.sessions.summary.record import read_summary
from kohakuterrarium.studio.sessions.summary.writer import count_turns
from kohakuterrarium.testing.llm import ScriptedLLM


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    s.init_meta("s", "agent", "", str(tmp_path), ["main", "helper"])
    s.save_conversation("main", [{"role": "user", "content": "Plan the migration"}])
    yield s
    s.close(update_status=False)


def _hook(store, settings, llm=None):
    return SummaryHook(
        store,
        loop=asyncio.get_running_loop(),
        llm_for=lambda agent, s: llm,
        settings=lambda: settings,
    )


async def _settle(hook):
    await asyncio.sleep(0)
    await hook.idle()


def test_event_agent_strips_the_sequence_suffix():
    assert event_agent("main:e000012") == "main"
    assert event_agent("ns:child:e3") == "ns:child"
    assert event_agent("plain") == "plain"


async def test_turn_end_of_the_primary_agent_writes_the_summary(store):
    hook = _hook(store, SummarySettings("heuristic", 5, ""))
    store.append_event("main", "text", {"content": "ignored"})
    store.append_event("helper", "processing_end", {})
    await _settle(hook)
    assert read_summary(store) == {}
    store.append_event("main", "processing_end", {})
    await _settle(hook)
    assert read_summary(store)["text"] == "Plan the migration"
    hook.detach()


async def test_compaction_event_passes_its_summary_text(store):
    hook = _hook(store, SummarySettings("compaction", 5, ""))
    store.append_event(
        "main", "compact_complete", {"summary": "Migrating auth to OIDC. More."}
    )
    await _settle(hook)
    assert read_summary(store)["text"] == "Migrating auth to OIDC."
    hook.detach()


async def test_requests_merge_per_agent_summing_turns(store, monkeypatch):
    calls = []
    gate = asyncio.Event()

    async def fake_summarize(target, settings, **kwargs):
        calls.append(kwargs)
        await gate.wait()

    monkeypatch.setattr(hook_mod, "summarize", fake_summarize)
    hook = _hook(store, SummarySettings("heuristic", 5, ""))
    base = {"compaction": "", "turns": 1, "reason": "turn"}
    hook.request({**base, "agent": "main"})
    await asyncio.sleep(0)
    hook.request({**base, "agent": "main"})
    hook.request(
        {**base, "agent": "main", "reason": "compaction", "compaction": "C", "turns": 0}
    )
    hook.request({**base, "agent": "main"})
    hook.request({**base, "agent": "helper"})
    gate.set()
    await hook.idle()
    assert [
        (c["agent"], c["reason"], c["new_turns"], c["compaction"]) for c in calls
    ] == [
        ("main", "turn", 1, ""),
        ("main", "compaction", 2, "C"),
        ("helper", "turn", 1, ""),
    ]
    hook.detach()


async def test_a_failing_refresh_does_not_stop_later_ones(store, monkeypatch):
    seen = []

    async def flaky(target, settings, **kwargs):
        seen.append(kwargs["agent"])
        if len(seen) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(hook_mod, "summarize", flaky)
    hook = _hook(store, SummarySettings("heuristic", 5, ""))
    hook.request({"agent": "main", "reason": "turn", "compaction": "", "turns": 1})
    await hook.idle()
    hook.request({"agent": "main", "reason": "turn", "compaction": "", "turns": 1})
    await hook.idle()
    assert seen == ["main", "main"]


async def test_detach_stops_listening_and_runs_on_store_close(tmp_path):
    s = SessionStore(str(tmp_path / "d.kohakutr"))
    s.init_meta("d", "agent", "", str(tmp_path), ["main"])
    hook = _hook(s, SummarySettings("heuristic", 1, ""))
    hook.detach()
    s.append_event("main", "processing_end", {})
    await _settle(hook)
    assert count_turns(s) == 0
    other = _hook(s, SummarySettings("heuristic", 1, ""))
    s.close(update_status=False)
    assert other._attached is False


async def test_llm_source_uses_the_hook_provider(store):
    llm = ScriptedLLM(["Auth migration plan"])
    hook = _hook(store, SummarySettings("llm", 5, ""), llm)
    store.append_event("main", "processing_end", {})
    await _settle(hook)
    assert read_summary(store)["text"] == "Auth migration plan"
    assert hook.llm_for("main", SummarySettings()) is llm
    hook.detach()
