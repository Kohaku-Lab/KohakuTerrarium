"""Service-level tests for :meth:`TerrariumService.chat_history_page`.

These exercise the same delegation a real HTTP paged request hits: the
``LocalTerrariumService`` reads a bounded physical event / snapshot slice and
wraps it in the contract envelope. The engine is a minimal stand-in backed by
a real :class:`SessionStore` so physical key ordering and history identity are
genuine.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from kohakuterrarium.session.history_paging import HistoryPagingError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.terrarium import history_service


@pytest.fixture()
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    yield s
    s.close()


def _events(store, content):
    for text in content:
        store.append_event("ag", "text", {"content": text})


def _fake_engine(store):
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


async def test_service_channel_page_reads_stored_records(store):
    store.save_channel_message("room", {"sender": "a", "content": "hello"})
    service = LocalTerrariumService(_fake_engine(store))
    page = await service.channel_history_page("g", "room", limit=5)
    assert [item["content"] for item in page["messages"]] == ["hello"]
    assert page["history_page"]["stream"] == "channel"


async def test_service_unknown_channel_page_is_an_empty_page(store):
    service = LocalTerrariumService(_fake_engine(store))
    page = await service.channel_history_page("g", "missing", limit=5)
    assert page["messages"] == []
    assert page["history_page"]["has_older"] is False


@pytest.mark.parametrize("target", ["chat", "channel"])
@pytest.mark.parametrize("read_kind", ["page", "detail"])
async def test_live_reads_follow_accepted_writes_off_loop(
    store, tmp_path, monkeypatch, target, read_kind
):
    store.append_event("ag", "text", {"content": "first"})
    store.save_channel_message("room", {"sender": "ag", "content": "first"})
    service = LocalTerrariumService(_fake_engine(store))
    args = ("ag",) if target == "chat" else ("g", "room")
    first = await getattr(service, f"{target}_history_page")(*args, limit=1)
    options = (
        {"limit": 3}
        if read_kind == "page"
        else {
            "stream": first["history_page"]["stream"],
            "history_id": first["history_page"]["history_id"],
            "ref": first["history_page"]["after"],
        }
    )
    release = threading.Event()
    entered = threading.Event()
    threads = []
    original = getattr(history_service, f"history_{read_kind}")

    def observed(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)

    def blocked():
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(history_service, f"history_{read_kind}", observed)
    store.submit(blocked)
    assert await asyncio.to_thread(entered.wait, 3)
    if target == "chat":
        store.submit(store.append_event, "ag", "text", {"content": "accepted"})
    else:
        store.submit(
            store.save_channel_message, "room", {"sender": "ag", "content": "accepted"}
        )
    reading = asyncio.create_task(
        getattr(service, f"{target}_history_{read_kind}")(*args, **options)
    )
    other = SessionStore(str(tmp_path / "other.kohakutr"))
    try:
        other.append_event("ag", "text", {"content": "independent"})
        await asyncio.sleep(0)
        unrelated = await LocalTerrariumService(_fake_engine(other)).chat_history_page(
            "ag"
        )
        assert unrelated["events"][0]["content"] == "independent"
        assert not reading.done(), "read overtook an accepted persistence operation"
        release.set()
        result = await reading
        assert threading.get_ident() not in threads
        if read_kind == "page":
            records = result["events"] if target == "chat" else result["messages"]
            assert [row["content"] for row in records] == ["first", "accepted"]
        else:
            assert result["record"]["content"] == "first"
    finally:
        release.set()
        await reading
        await asyncio.to_thread(other.close)
