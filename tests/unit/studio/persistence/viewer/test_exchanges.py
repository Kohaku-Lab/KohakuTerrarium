"""Unit tests for :mod:`kohakuterrarium.studio.persistence.viewer.exchanges`."""

import pytest

import kohakuterrarium.session.run_state as rs
from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.exchanges import (
    EXCHANGE_TEXT_LIMIT,
    MAX_EXCHANGES,
    build_exchanges_payload,
)


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    s.init_meta("s", "terrarium", "", str(tmp_path), ["root", "worker"])
    s.meta["name"] = "Docs sweep"
    s.meta["summary"] = {"text": "Rewriting the docs", "source": "user"}
    rs.set_lifecycle(s, live=False, stop_reason=rs.STOP_USER)
    messages = []
    for n in range(1, 6):
        messages += [
            {"role": "user", "content": f"prompt {n}"},
            {"role": "assistant", "content": f"reply {n}"},
        ]
    s.save_conversation("root", messages)
    s.save_conversation(
        "worker", [{"role": "user", "content": "w" * (EXCHANGE_TEXT_LIMIT + 50)}]
    )
    yield s
    s.close(update_status=False)


def test_default_is_the_last_three_exchanges_of_the_primary_agent(store):
    payload = build_exchanges_payload(store, "s")
    assert (payload["agent"], payload["turn_count"]) == ("root", 5)
    assert [(e["turn"], e["user"], e["reply"]) for e in payload["exchanges"]] == [
        (3, "prompt 3", "reply 3"),
        (4, "prompt 4", "reply 4"),
        (5, "prompt 5", "reply 5"),
    ]
    assert (payload["title"], payload["summary"], payload["summary_source"]) == (
        "Docs sweep",
        "Rewriting the docs",
        "user",
    )
    assert payload["stop_reason"] == "user"
    assert payload["agents"] == ["root", "worker"]


def test_limit_is_clamped_and_agent_selectable(store):
    assert len(build_exchanges_payload(store, "s", limit=0)["exchanges"]) == 1
    assert len(build_exchanges_payload(store, "s", limit=999)["exchanges"]) == 5
    assert MAX_EXCHANGES >= 5
    worker = build_exchanges_payload(store, "s", agent="worker")
    assert len(worker["exchanges"][0]["user"]) == EXCHANGE_TEXT_LIMIT


def test_unknown_agent_is_not_found(store):
    with pytest.raises(NotFoundError):
        build_exchanges_payload(store, "s", agent="ghost")


def test_a_session_without_agents_has_no_exchanges(tmp_path):
    s = SessionStore(str(tmp_path / "e.kohakutr"))
    s.init_meta("e", "agent", "", str(tmp_path), [])
    try:
        payload = build_exchanges_payload(s, "e")
        assert (payload["agent"], payload["exchanges"], payload["stop_reason"]) == (
            "",
            [],
            None,
        )
    finally:
        s.close(update_status=False)
