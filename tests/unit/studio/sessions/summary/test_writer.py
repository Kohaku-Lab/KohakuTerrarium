"""Unit tests for :mod:`kohakuterrarium.studio.sessions.summary.writer`."""

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.identity.session_summary import SummarySettings
from kohakuterrarium.studio.sessions.summary import writer
from kohakuterrarium.studio.sessions.summary.record import read_summary, write_summary
from kohakuterrarium.testing.llm import ScriptedLLM

CONVERSATION = [
    {"role": "user", "content": "Fix the flaky upload test"},
    {"role": "assistant", "content": "Found a race in the retry loop."},
]


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.kohakutr"))
    s.init_meta("s", "agent", "", str(tmp_path), ["main", "helper"])
    s.save_conversation("main", CONVERSATION)
    yield s
    s.close(update_status=False)


def _never():
    raise AssertionError("no model call expected")


async def _turn(store, settings, llm=None, **kwargs):
    return await writer.summarize(
        store,
        settings,
        llm_factory=(lambda: llm) if llm is not None else _never,
        agent="main",
        new_turns=1,
        **kwargs,
    )


def test_is_due_first_turn_every_n_and_always_on_other_reasons():
    assert writer.is_due({}, 1, 5, writer.TURN)
    assert not writer.is_due({"at_turn": 1}, 5, 5, writer.TURN)
    assert writer.is_due({"at_turn": 1}, 6, 5, writer.TURN)
    assert writer.is_due({"at_turn": 9}, 9, 5, writer.COMPACTED)
    assert writer.is_due({"at_turn": 9}, 9, 5, writer.MANUAL)


def test_count_turns_only_grows(store):
    assert writer.count_turns(store) == 0
    assert writer.count_turns(store, 2) == 2
    assert writer.count_turns(store, 0) == 2
    assert writer.count_turns(store, 1) == 3


async def test_heuristic_source_refreshes_on_turn_one_then_every_n(store):
    settings = SummarySettings("heuristic", 3, "")
    first = await _turn(store, settings)
    assert (first["text"], first["source"], first["at_turn"]) == (
        "Fix the flaky upload test",
        "heuristic",
        1,
    )
    assert await _turn(store, settings) is None
    assert await _turn(store, settings) is None
    store.save_conversation("main", [{"role": "user", "content": "Ship it"}])
    fourth = await _turn(store, settings)
    assert (fourth["text"], fourth["at_turn"]) == ("Ship it", 4)


async def test_llm_source_writes_a_quick_heuristic_then_the_model_line(
    store, monkeypatch
):
    seen = []
    llm = ScriptedLLM(["Upload test race fix"])

    def spy(target, text, source, at_turn):
        seen.append(source)
        return write_summary(target, text, source, at_turn)

    monkeypatch.setattr(writer, "write_summary", spy)
    written = await _turn(store, SummarySettings("llm", 5, ""), llm)
    assert seen == ["heuristic", "llm"]
    assert (written["text"], written["source"]) == ("Upload test race fix", "llm")


async def test_a_failed_model_call_never_downgrades_an_llm_summary(store):
    write_summary(store, "Upload test race fix", "llm", 1)
    writer.count_turns(store, 1)

    class Broken:
        async def chat(self, *_a, **_k):
            raise RuntimeError("provider down")
            yield ""

    settings = SummarySettings("llm", 1, "")
    assert await _turn(store, settings, Broken()) is None
    assert read_summary(store)["text"] == "Upload test race fix"


async def test_compaction_source_uses_the_compaction_summary(store):
    settings = SummarySettings("compaction", 5, "")
    written = await writer.summarize(
        store,
        settings,
        llm_factory=_never,
        reason=writer.COMPACTED,
        agent="main",
        compaction="The user is stabilising CI. Details follow.",
    )
    assert (written["text"], written["source"]) == (
        "The user is stabilising CI.",
        "compaction",
    )
    assert await _turn(store, SummarySettings("compaction", 1, "")) is None
    assert read_summary(store)["source"] == "compaction"


async def test_other_agents_off_and_user_summaries_are_left_alone(store):
    settings = SummarySettings("heuristic", 1, "")
    assert (
        await writer.summarize(
            store, settings, llm_factory=_never, agent="helper", new_turns=1
        )
        is None
    )
    assert writer.count_turns(store) == 0
    assert await _turn(store, SummarySettings("off", 1, "")) is None
    assert read_summary(store) == {}
    write_summary(store, "Mine", "user", 0)
    assert await _turn(store, settings) is None
    assert read_summary(store)["text"] == "Mine"


async def test_manual_replaces_a_user_summary_even_when_off(store):
    write_summary(store, "Mine", "user", 0)
    llm = ScriptedLLM(["Regenerated line"])
    written = await writer.summarize(
        store,
        SummarySettings("off", 5, ""),
        llm_factory=lambda: llm,
        reason=writer.MANUAL,
    )
    assert (written["text"], written["source"]) == ("Regenerated line", "llm")


async def test_compose_falls_back_llm_to_compaction_to_heuristic():
    assert await writer.compose(
        "llm", CONVERSATION, "Sentence one. Two.", lambda: None
    ) == (
        "Sentence one.",
        "compaction",
    )
    assert await writer.compose("llm", CONVERSATION, "", lambda: None) == (
        "Fix the flaky upload test",
        "heuristic",
    )
    assert await writer.compose("heuristic", CONVERSATION, "Ignored.", _never) == (
        "Fix the flaky upload test",
        "heuristic",
    )
