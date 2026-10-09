"""Unit tests for :mod:`kohakuterrarium.studio.sessions.summary.edit`."""

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.identity.session_summary import save_settings
from kohakuterrarium.studio.sessions.summary import edit
from kohakuterrarium.studio.sessions.summary.record import read_summary, write_summary


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    s.init_meta("s", "agent", "", str(tmp_path), ["main"])
    s.save_conversation("main", [{"role": "user", "content": "Tune the cache sizes"}])
    yield s
    s.close(update_status=False)


async def test_user_text_is_stored_cleaned_and_empty_text_clears_it(store):
    written = await edit.set_user_summary(store, '  "Cache   tuning"  ')
    assert (written["text"], written["source"]) == ("Cache tuning", "user")
    assert read_summary(store) == written
    assert await edit.set_user_summary(store, "   ") == {}
    assert read_summary(store) == {}


async def test_regenerate_on_a_saved_store_falls_back_to_heuristic(store, monkeypatch):
    monkeypatch.setenv("KT_SESSION_SUMMARY_SOURCE", "llm")
    write_summary(store, "Mine", "user", 0)
    written = await edit.regenerate(store)
    assert (written["text"], written["source"]) == ("Tune the cache sizes", "heuristic")


async def test_regenerate_on_a_saved_store_uses_the_summary_model(
    store, monkeypatch, scripted
):
    monkeypatch.setenv("KT_SESSION_SUMMARY_SOURCE", "llm")
    save_settings({"model": "profile/summary"})
    scripted["script"] = ["Cache size tuning"]
    written = await edit.regenerate(store)
    assert (written["text"], written["source"]) == ("Cache size tuning", "llm")


async def test_regenerate_returns_the_current_summary_when_nothing_is_written(tmp_path):
    s = SessionStore(str(tmp_path / "empty.kohakutr"))
    s.init_meta("e", "agent", "", str(tmp_path), [])
    try:
        assert await edit.regenerate(s) == {}
    finally:
        s.close(update_status=False)
