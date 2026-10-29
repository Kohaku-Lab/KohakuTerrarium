"""Unit tests for :mod:`kohakuterrarium.core.pinned`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.pinned import (
    PinnedContext,
    pin_message,
    state_key,
    sync_with_store,
    unpin_message,
)
from kohakuterrarium.llm.message import ImagePart, TextPart

PIXEL = "data:image/png;base64,iVBORw0KGgo="


def _wire(messages):
    return [{"role": role, "content": text} for role, text in messages]


class _Store:
    def __init__(self, state=None):
        self.state = dict(state or {})


def _agent(name="bot", store=None):
    return SimpleNamespace(
        config=SimpleNamespace(name=name),
        controller=SimpleNamespace(pinned=PinnedContext()),
        session_store=store,
    )


class TestPinnedContext:
    def test_pins_follow_the_system_block_in_pin_order(self):
        pins = PinnedContext()
        pins.pin("a", "user", "first")
        pins.pin("b", "user", "second")
        wire = pins.apply(_wire([("system", "S"), ("user", "hi"), ("assistant", "yo")]))
        assert [m["content"] for m in wire] == ["S", "first", "second", "hi", "yo"]
        assert pins.context_end(wire) == 3

    def test_context_end_with_system_role_pins(self):
        pins = PinnedContext()
        pins.pin("rule", "system", "R")
        pins.pin("persona", "user", "P")
        wire = pins.apply(_wire([("system", "S"), ("user", "hi")]))
        assert [m["content"] for m in wire] == ["S", "R", "P", "hi"]
        assert pins.context_end(wire) == 3

    def test_replace_keeps_position_and_unpin_removes(self):
        pins = PinnedContext()
        pins.pin("a", "user", "first")
        pins.pin("b", "user", "second")
        pins.pin("a", "user", "first v2")
        assert [k for k, _ in pins.pinned()] == ["a", "b"]
        assert pins.pinned()[0][1].get_text_content() == "first v2"
        assert pins.unpin("a") is True
        assert pins.unpin("a") is False
        assert [k for k, _ in pins.pinned()] == ["b"]

    def test_no_pins_returns_messages_unchanged(self):
        pins = PinnedContext()
        wire = _wire([("system", "S"), ("user", "hi")])
        assert pins.apply(wire) is wire
        assert pins.context_end(wire) == 1

    def test_no_system_block_puts_pins_first(self):
        pins = PinnedContext()
        pins.pin("a", "user", "P")
        assert [m["content"] for m in pins.apply(_wire([("user", "hi")]))] == [
            "P",
            "hi",
        ]

    def test_multimodal_pin_wire_shape_and_accounting(self):
        pins = PinnedContext()
        pins.pin("persona", "user", [TextPart(text="look"), ImagePart(url=PIXEL)])
        wire = pins.apply(_wire([("system", "S")]))
        assert wire[1]["role"] == "user"
        assert [p["type"] for p in wire[1]["content"]] == ["text", "image_url"]
        assert "metadata" not in wire[1]
        assert pins.text_length() == 4 and pins.image_count() == 1

    def test_apply_returns_fresh_dicts_each_call(self):
        pins = PinnedContext()
        pins.pin("a", "user", "P")
        first = pins.apply(_wire([("system", "S")]))
        first[1]["content"] = "mutated by a hook"
        assert pins.apply(_wire([("system", "S")]))[1]["content"] == "P"

    @pytest.mark.parametrize("role", ["assistant", "tool", ""])
    def test_rejects_roles_other_than_user_or_system(self, role):
        with pytest.raises(ValueError, match="pin role"):
            PinnedContext().pin("a", role, "x")

    def test_rejects_empty_key(self):
        with pytest.raises(ValueError, match="non-empty"):
            PinnedContext().pin("", "user", "x")

    def test_state_round_trip_keeps_order_and_images(self):
        pins = PinnedContext()
        pins.pin("a", "user", [TextPart(text="look"), ImagePart(url=PIXEL)])
        pins.pin("b", "system", "rule")
        again = PinnedContext()
        again.load_state(pins.to_state())
        assert [k for k, _ in again.pinned()] == ["a", "b"]
        assert again.apply([]) == pins.apply([])

    def test_load_state_skips_malformed_entries(self):
        pins = PinnedContext()
        pins.load_state(
            [
                {"key": "ok", "message": {"role": "user", "content": "x"}},
                {"message": {"role": "user", "content": "no key"}},
                {"key": "bad", "message": {"role": "tool", "content": "x"}},
                "garbage",
            ]
        )
        assert [k for k, _ in pins.pinned()] == ["ok"]


class TestAgentHelpers:
    def test_pin_and_unpin_persist_to_the_store(self):
        store = _Store()
        agent = _agent(store=store)
        pin_message(agent, "persona", "user", "hello")
        assert store.state[state_key("bot")][0]["key"] == "persona"
        assert unpin_message(agent, "persona") is True
        assert store.state[state_key("bot")] == []
        assert unpin_message(agent, "persona") is False

    def test_pin_without_store_stays_in_memory(self):
        agent = _agent()
        pin_message(agent, "persona", "user", "hello")
        assert len(agent.controller.pinned) == 1

    def test_pin_without_controller_raises(self):
        agent = SimpleNamespace(config=SimpleNamespace(name="x"), controller=None)
        with pytest.raises(RuntimeError, match="controller"):
            pin_message(agent, "k", "user", "x")
        assert unpin_message(agent, "k") is False

    def test_sync_loads_saved_and_memory_pins_win_by_key(self):
        saved = PinnedContext()
        saved.pin("a", "user", "saved a")
        saved.pin("b", "user", "saved b")
        store = _Store({state_key("bot"): saved.to_state()})
        agent = _agent()
        agent.controller.pinned.pin("b", "user", "fresh b")
        agent.controller.pinned.pin("c", "user", "fresh c")
        sync_with_store(agent, store)
        texts = [m.get_text_content() for _, m in agent.controller.pinned.pinned()]
        assert texts == ["saved a", "fresh b", "fresh c"]
        assert [e["key"] for e in store.state[state_key("bot")]] == ["a", "b", "c"]

    def test_sync_twice_does_not_duplicate(self):
        store = _Store()
        agent = _agent()
        agent.controller.pinned.pin("a", "user", "x")
        sync_with_store(agent, store)
        sync_with_store(agent, store)
        assert len(agent.controller.pinned) == 1
        assert len(store.state[state_key("bot")]) == 1

    def test_sync_with_nothing_writes_nothing(self):
        store = _Store()
        sync_with_store(_agent(), store)
        assert store.state == {}
