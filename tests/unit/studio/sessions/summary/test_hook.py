"""Unit tests for :mod:`kohakuterrarium.studio.sessions.summary.hook`."""

import asyncio
import json

import pytest

from kohakuterrarium.session.output.output import SessionOutput
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


def _save_side(store, *, parent="main", job="side-job", run=0, turn=1):
    meta = {
        "job_id": job,
        "name": "fork",
        "run": run,
        "audience": "user",
        "side_turn": turn,
        "title": "Why does bread rise?",
        "status": "done",
        "inherited_message_count": 1,
    }
    store.save_subagent(
        parent,
        "fork",
        run,
        meta,
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "PRIVATE parent database topic"},
                    {
                        "role": "user",
                        "content": "[Private context fork instructions] PRIVATE policy",
                    },
                    {"role": "user", "content": "Why does bread rise?"},
                ]
            }
        ),
    )
    return meta


async def test_session_output_flattens_btw_metadata_into_side_title_event(store):
    provider_calls = []
    settings = SummarySettings("llm", 3, "titles-profile")
    llm = ScriptedLLM(["Bread fermentation"])

    def provider(agent, selected):
        provider_calls.append((agent, selected))
        return llm

    hook = SummaryHook(
        store,
        loop=asyncio.get_running_loop(),
        llm_for=provider,
        settings=lambda: settings,
    )
    meta = _save_side(store, parent="helper")
    output = SessionOutput("helper", store, None)
    output._record_activity("btw_result", "Side conversation finished", "", meta)
    await output.drain()
    await _settle(hook)
    assert (
        store.load_subagent_meta("helper", "fork", 0)["title"] == "Bread fermentation"
    )
    assert provider_calls == [("helper", settings)]
    assert "bread" in llm.last_user_message
    assert "PRIVATE" not in str(llm.call_log)
    assert read_summary(store) == {}
    assert count_turns(store) == 0
    hook.detach()


async def test_side_refresh_coalesces_without_overlapping_shared_provider(store):
    started = asyncio.Event()
    release = asyncio.Event()

    class GatedLLM(ScriptedLLM):
        active = 0
        max_active = 0

        async def chat(self, *args, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                started.set()
                await release.wait()
                async for chunk in super().chat(*args, **kwargs):
                    yield chunk
            finally:
                self.active -= 1

    llm = GatedLLM(["Old side title", "Parent migration", "Latest side title"])
    hook = SummaryHook(
        store,
        loop=asyncio.get_running_loop(),
        llm_for=lambda *_: llm,
        settings=lambda: SummarySettings("llm", 1),
    )
    first = _save_side(store)
    store.append_event("main", "activity:btw_result", first)
    await asyncio.wait_for(started.wait(), 2)
    second = _save_side(store, turn=2)
    third = _save_side(store, turn=3)
    hook.request_side("main", second)
    hook.request_side("main", third)
    hook.request_side("main", second)
    hook.request({"agent": "main", "reason": "turn", "compaction": "", "turns": 1})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(hook._task), 0.1)
    assert store.load_subagent_meta("main", "fork", 0)["title"] == first["title"]
    release.set()
    await hook.idle()
    meta = store.load_subagent_meta("main", "fork", 0)
    assert meta["title"] == "Latest side title"
    assert meta["title_at_turn"] == 3
    assert llm.max_active == 1
    assert llm.call_count == 3
    assert read_summary(store)["text"] == "Parent migration"
    assert count_turns(store) == 1
    assert hook._side_tasks == {}
    hook.detach()


async def test_side_jobs_have_independent_keys_and_bad_results_are_ignored(store):
    llm = ScriptedLLM(["Bread fermentation", "Bread starter"])
    hook = _hook(store, SummarySettings("llm", 5), llm)
    first = _save_side(store)
    second = _save_side(store, job="second-job", run=1)
    store.append_event("main", "btw_result", first)
    store.append_event("main", "btw_result", second)
    for changes in (
        {"audience": "controller"},
        {"job_id": ""},
        {"job_id": []},
        {"name": None},
        {"name": ""},
        {"run": -1},
        {"run": True},
        {"side_turn": 0},
        {"side_turn": "1"},
    ):
        store.append_event("main", "btw_result", {**first, **changes})
    await _settle(hook)
    assert {
        store.load_subagent_meta("main", "fork", run)["title"] for run in (0, 1)
    } == {"Bread fermentation", "Bread starter"}
    assert llm.call_count == 2
    assert read_summary(store) == {}
    hook.detach()
    store.append_event("main", "btw_result", first)
    await _settle(hook)
    assert llm.call_count == 2
