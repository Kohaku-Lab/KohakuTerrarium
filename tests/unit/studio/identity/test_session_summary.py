"""Unit tests for :mod:`kohakuterrarium.studio.identity.session_summary`."""

import json

import pytest

from kohakuterrarium.studio.identity import session_summary as ss


def test_defaults_are_llm_every_five_turns_session_model(monkeypatch):
    monkeypatch.delenv(ss.SOURCE_ENV, raising=False)
    assert ss.load_settings() == ss.SummarySettings("llm", 5, "")


def test_save_merges_validates_and_round_trips_through_the_file(monkeypatch):
    monkeypatch.delenv(ss.SOURCE_ENV, raising=False)
    ss.save_settings({"every_n_turns": 3})
    ss.save_settings({"model": "  openai/gpt-x  "})
    stored = json.loads(ss.settings_path().read_text("utf-8"))
    assert stored == {"source": "llm", "every_n_turns": 3, "model": "openai/gpt-x"}
    assert ss.load_settings() == ss.SummarySettings("llm", 3, "openai/gpt-x")


@pytest.mark.parametrize(
    "bad",
    [
        {"source": "magic"},
        {"every_n_turns": 0},
        {"every_n_turns": True},
        {"every_n_turns": "4"},
        {"model": 3},
    ],
)
def test_bad_values_are_rejected_and_nothing_is_written(bad, monkeypatch):
    monkeypatch.delenv(ss.SOURCE_ENV, raising=False)
    with pytest.raises(ValueError):
        ss.save_settings(bad)
    assert not ss.settings_path().exists()


def test_a_corrupt_file_reads_as_defaults(monkeypatch):
    monkeypatch.delenv(ss.SOURCE_ENV, raising=False)
    ss.settings_path().parent.mkdir(parents=True, exist_ok=True)
    ss.settings_path().write_text("{nope", encoding="utf-8")
    assert ss.load_settings() == ss.SummarySettings()
    ss.settings_path().write_text('{"source": "magic"}', encoding="utf-8")
    assert ss.load_settings() == ss.SummarySettings()


def test_env_overrides_only_the_source(monkeypatch):
    monkeypatch.delenv(ss.SOURCE_ENV, raising=False)
    ss.save_settings({"source": "compaction", "every_n_turns": 2})
    monkeypatch.setenv(ss.SOURCE_ENV, "off")
    assert ss.load_settings() == ss.SummarySettings("off", 2, "")
    assert ss.load_settings(apply_env=False).source == "compaction"
    monkeypatch.setenv(ss.SOURCE_ENV, "bogus")
    assert ss.load_settings().source == "compaction"
