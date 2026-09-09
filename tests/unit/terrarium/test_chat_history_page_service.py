"""Service-level tests for :meth:`TerrariumService.chat_history_page`.

These exercise the same delegation a real HTTP paged request hits: the
``LocalTerrariumService`` reads a bounded physical event / snapshot slice and
wraps it in the contract envelope. The engine is a minimal stand-in backed by
a real :class:`SessionStore` so physical key ordering and history identity are
genuine.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kohakuterrarium.session.history_paging import HistoryPagingError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.service import LocalTerrariumService


@pytest.fixture()
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    yield s
    s.close()


def _events(store, content):
    for text in content:
        store.append_event("ag", "text", {"content": text})


def _fake_engine(store, env=None):
    agent = SimpleNamespace(
        session_store=store,
        conversation_history=[{"role": "user", "content": "snap"}],
        is_processing=True,
        _direct_job_meta={"job-x": 1},
        subagent_manager=SimpleNamespace(get_running_jobs=lambda: []),
        executor=SimpleNamespace(get_running_jobs=lambda: []),
    )
    creature = SimpleNamespace(agent=agent, graph_id="g", name="ag")
    return SimpleNamespace(
        get_creature=lambda cid: creature,
        _session_stores={"g": store},
        _environments={"g": env} if env is not None else {},
    )


async def test_service_event_page_matches_contract(store):
    _events(store, ["c0", "c1", "c2", "c3", "c4"])
    service = LocalTerrariumService(_fake_engine(store))
    page = await service.chat_history_page("ag", stream="events", limit=2)
    assert [item["content"] for item in page["events"]] == ["c3", "c4"]
    assert page["messages"] == []
    hp = page["history_page"]
    assert hp["version"] == 1
    assert hp["stream"] == "events"
    assert hp["has_older"] is True
    assert hp["has_newer"] is False
    assert hp["reset_required"] is False
    assert page["is_processing"] is True
    assert page["live_job_ids"] == ["job-x"]


async def test_service_snapshot_page_matches_contract(store):
    service = LocalTerrariumService(_fake_engine(store))
    page = await service.chat_history_page("ag", stream="snapshot", limit=1)
    assert [item["content"] for item in page["messages"]] == ["snap"]
    assert page["events"] == []
    assert page["history_page"]["stream"] == "snapshot"


async def test_service_unsupported_stream_is_explicit(store):
    service = LocalTerrariumService(_fake_engine(store))
    with pytest.raises(HistoryPagingError):
        await service.chat_history_page("ag", stream="bogus", limit=2)


async def test_service_nonpositive_limit_rejected(store):
    service = LocalTerrariumService(_fake_engine(store))
    with pytest.raises(HistoryPagingError):
        await service.chat_history_page("ag", stream="events", limit=0)


async def test_service_unknown_channel_page_is_not_an_empty_page(store):
    service = LocalTerrariumService(_fake_engine(store))
    with pytest.raises(KeyError):
        await service.channel_history_page("g", "missing", limit=5)


async def test_service_channel_page_reads_stored_records(store):
    store.save_channel_message("room", {"sender": "a", "content": "hello"})
    service = LocalTerrariumService(_fake_engine(store))
    page = await service.channel_history_page("g", "room", limit=5)
    assert [item["content"] for item in page["messages"]] == ["hello"]
    assert page["history_page"]["stream"] == "channel"


async def test_service_live_empty_channel_pages_without_error(store):
    env = SimpleNamespace(shared_channels={"room": object()})
    service = LocalTerrariumService(_fake_engine(store, env=env))
    page = await service.channel_history_page("g", "room", limit=5)
    assert page["messages"] == []
    assert page["history_page"]["has_older"] is False
