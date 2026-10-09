"""Unit tests for :mod:`kohakuterrarium.studio.sessions.summary.sources`."""

from kohakuterrarium.core.conversation_elide import TOOL_FEEDBACK_KIND
from kohakuterrarium.studio.sessions.summary import sources
from kohakuterrarium.testing.llm import ScriptedLLM

MESSAGES = [
    {"role": "user", "content": "x", "metadata": {"kind": TOOL_FEEDBACK_KIND}},
    {"role": "user", "content": "  Refactor   the\nparser module  "},
    {"role": "assistant", "content": "Done refactoring."},
    {"role": "user", "content": "Now add tests"},
    {"role": "assistant", "content": "Added 12 tests."},
]


def test_one_line_collapses_strips_quotes_and_cuts_on_a_word():
    assert sources.one_line('  "a\n\tb"  ') == "a b"
    cut = sources.one_line("alpha beta gamma delta", limit=12)
    assert cut == "alpha beta…"
    assert len(cut) <= 12
    assert sources.one_line("x" * 30, limit=10) == "x" * 9 + "…"
    assert sources.one_line("") == ""


def test_heuristic_is_the_cleaned_first_real_prompt():
    assert sources.first_prompt(MESSAGES).startswith("Refactor")
    assert sources.heuristic_text(MESSAGES) == "Refactor the parser module"
    assert sources.heuristic_text([]) == ""
    long = [{"role": "user", "content": "word " * 40}]
    assert len(sources.heuristic_text(long)) <= sources.HEURISTIC_LIMIT


def test_compaction_text_takes_the_first_sentence():
    text = "## The user is building a parser. They asked for tests. More."
    assert sources.compaction_text(text) == "The user is building a parser."
    assert sources.compaction_text("   ") == ""
    assert sources.compaction_text("用户在写解析器。然后") == "用户在写解析器。"


def test_llm_request_carries_opening_compaction_and_latest_exchanges():
    request = sources.llm_request(MESSAGES, compaction="earlier work on lexing")
    assert request[0] == {"role": "system", "content": sources.SUMMARY_PROMPT}
    body = request[1]["content"]
    assert body.startswith("Opening request:\nRefactor the parser module")
    assert "Summary of earlier context:\nearlier work on lexing" in body
    assert "User: Now add tests\nAssistant: Added 12 tests." in body
    assert body.endswith(sources.LLM_MARKER)
    assert (
        "Summary of earlier context" not in sources.llm_request(MESSAGES)[1]["content"]
    )


async def test_llm_text_keeps_the_first_nonblank_line_without_a_period():
    llm = ScriptedLLM(['\n"Parser refactor and tests."\nextra chatter'])
    assert await sources.llm_text(llm, MESSAGES) == "Parser refactor and tests"
    assert llm.call_log[0][1]["content"].endswith(sources.LLM_MARKER)


async def test_llm_text_skips_the_call_without_a_prompt():
    llm = ScriptedLLM(["never"])
    assert await sources.llm_text(llm, [{"role": "assistant", "content": "hi"}]) == ""
    assert llm.call_count == 0
    assert await sources.llm_text(ScriptedLLM(["   "]), MESSAGES) == ""
