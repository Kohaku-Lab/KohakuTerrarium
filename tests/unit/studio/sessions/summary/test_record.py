"""Unit tests for :mod:`kohakuterrarium.studio.sessions.summary.record`."""

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.summary import record


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    yield s
    s.close(update_status=False)


def test_write_read_and_clear_round_trip_through_meta(store):
    assert record.read_summary(store) == {}
    written = record.write_summary(store, "Parser work", "llm", 3)
    assert written["text"] == "Parser work"
    assert record.read_summary(store) == written
    assert record.read_summary(store.load_meta()) == written
    record.clear_summary(store)
    assert record.read_summary(store) == {}


def test_read_ignores_malformed_values(store):
    store.meta["summary"] = "a bare string"
    assert record.read_summary(store) == {}
    store.meta["summary"] = {"source": "llm"}
    assert record.read_summary(store) == {}


@pytest.mark.parametrize(
    "held, source, configured, expected",
    [
        (None, "heuristic", "llm", True),
        ("user", "llm", "llm", False),
        ("llm", "heuristic", "llm", False),
        ("llm", "compaction", "llm", False),
        ("heuristic", "compaction", "llm", True),
        ("heuristic", "heuristic", "llm", True),
        ("llm", "heuristic", "heuristic", True),
        ("compaction", "heuristic", "compaction", False),
    ],
)
def test_should_replace_never_downgrades_except_to_the_configured_source(
    held, source, configured, expected
):
    current = {"text": "t", "source": held} if held else {}
    assert record.should_replace(current, source, configured) is expected
