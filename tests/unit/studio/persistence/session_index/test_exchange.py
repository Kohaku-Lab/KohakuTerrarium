"""Unit tests for :mod:`kohakuterrarium.studio.persistence.session_index.exchange`."""

import json

from kohakuterrarium.core.conversation_elide import TOOL_FEEDBACK_KIND
from kohakuterrarium.studio.persistence.session_index.exchange import (
    EMPTY_EXCHANGE,
    flatten_text,
    is_user_prompt,
    latest_exchange,
    messages_of,
    primary_agent,
    recent_exchanges,
)


def _user(text, **extra):
    return {"role": "user", "content": text, **extra}


def _assistant(text):
    return {"role": "assistant", "content": text}


def _feedback(text):
    return _user(text, metadata={"kind": TOOL_FEEDBACK_KIND})


CONVERSATION = [
    {"role": "system", "content": "sys"},
    _user("first question"),
    _assistant("thinking"),
    {"role": "tool", "content": "tool output"},
    _feedback("tool says hi"),
    _assistant("first answer"),
    _user("   "),
    _user("second question"),
    _assistant(""),
    _user("third question"),
    _assistant([{"type": "text", "text": "third"}, {"type": "image_url"}]),
]


def test_flatten_text_marks_attachments_and_bounds_length():
    assert flatten_text(None) == ""
    assert flatten_text("x" * 50, limit=5) == "xxxxx"
    parts = [
        "bare",
        {"type": "text", "text": "hi"},
        {"type": "image"},
        {"type": "file"},
        {"type": "audio"},
        {},
    ]
    assert flatten_text(parts) == "bare hi [image] [file] [audio] [attachment]"
    assert flatten_text({"type": "text", "text": "one"}) == "one"
    assert flatten_text(42) == "42"


def test_primary_agent_prefers_viewer_default_then_root_then_first():
    assert primary_agent({"agents": ["a", "root"], "viewer_default_agent": "a"}) == "a"
    assert (
        primary_agent({"agents": ["a", "root"], "viewer_default_agent": "x"}) == "root"
    )
    assert primary_agent({"agents": ["b", "c"]}) == "b"
    assert primary_agent({}) == ""


def test_messages_of_accepts_lists_wrapped_and_json_snapshots():
    assert messages_of([_user("a")]) == [_user("a")]
    assert messages_of({"messages": [_user("a")]}) == [_user("a")]
    assert messages_of(json.dumps({"messages": [_user("a")]})) == [_user("a")]
    assert messages_of(json.dumps([_user("a")]).encode()) == [_user("a")]
    assert messages_of("not json") is None
    assert messages_of(None) is None


def test_is_user_prompt_skips_tool_feedback_and_other_roles():
    assert is_user_prompt(_user("x"))
    assert not is_user_prompt(_feedback("x"))
    assert not is_user_prompt(_assistant("x"))
    assert not is_user_prompt("x")


def test_recent_exchanges_pair_each_prompt_with_its_last_reply():
    assert recent_exchanges(CONVERSATION, 10) == [
        {"turn": 1, "user": "first question", "reply": "first answer"},
        {"turn": 2, "user": "second question", "reply": ""},
        {"turn": 3, "user": "third question", "reply": "third [image]"},
    ]
    assert [e["turn"] for e in recent_exchanges(CONVERSATION, 2)] == [2, 3]
    assert recent_exchanges(CONVERSATION, 0) == []
    assert recent_exchanges(None) == []
    assert recent_exchanges([_assistant("orphan")]) == []


def test_recent_exchanges_bound_each_text():
    long = [_user("u" * 50), _assistant("a" * 50)]
    assert recent_exchanges(long, 1, limit=8) == [
        {"turn": 1, "user": "u" * 8, "reply": "a" * 8}
    ]


def test_latest_exchange_counts_prompts_and_takes_the_last_one():
    assert latest_exchange(CONVERSATION) == {
        "last_user": "third question",
        "last_reply": "third [image]",
        "turn_count": 3,
    }
    assert latest_exchange([]) == EMPTY_EXCHANGE
    assert latest_exchange([_feedback("only feedback")]) == EMPTY_EXCHANGE
    empty = latest_exchange(None)
    empty["last_user"] = "mutated"
    assert EMPTY_EXCHANGE["last_user"] == ""
