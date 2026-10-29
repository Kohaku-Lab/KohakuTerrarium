"""Unit tests for :mod:`kohakuterrarium.builtins.kt_ui.inline_state`.

Block state is accepted only for a block that exists in the recorded reply of
its turn and branch, only for keys the block declares, within the size cap;
accepted state reaches every other output and comes back from the session.
"""

import asyncio
import json
from pathlib import Path

from kohakuterrarium.llm.message import TextPart
from kohakuterrarium.builtins.kt_ui import inline_state as mod
from kohakuterrarium.builtins.kt_ui.inline_state import (
    InlineUiStates,
    block_hash,
    block_ids,
)
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.output import OutputRecorder

BLOCK = '<ui><state people={3} note=""/><slider bind="people" min={1} max={9}/></ui>'
REPLY = f"Here:\n\n{BLOCK}\n\nAnd again:\n{BLOCK}\n"
FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "kt_ui_markup.json"


class Recorder(OutputRecorder):
    def __init__(self):
        super().__init__()
        self.widget_events = []

    async def on_widget(self, event):
        self.widget_events.append(event)


class Agent:
    class config:
        name = "creature"

    def __init__(self, store=None):
        self.session_store = store


def _store(tmp_path, reply=REPLY, turn=1, branch=1):
    store = SessionStore(tmp_path / "s.kohakutr")
    store.append_event(
        "creature",
        "assistant_message",
        {"message": {"role": "assistant", "content": reply}},
        turn_index=turn,
        branch_id=branch,
    )
    return store


def _service(store):
    out, other = Recorder(), Recorder()
    router = OutputRouter(out)
    router.add_secondary(other)
    service = InlineUiStates(router)
    service.bind_agent(Agent(store))
    return service, out, other


def _first_id():
    return block_ids(REPLY)[0][0]


class TestBlockIds:
    def test_hash_is_fnv1a_of_utf8(self):
        assert block_hash("") == "811c9dc5"
        assert block_hash("a") == "e40c292c"
        assert block_hash("é") != block_hash("e")

    def test_identical_blocks_get_occurrence_numbers(self):
        ids = [bid for bid, _ in block_ids(REPLY)]
        digest = block_hash(BLOCK)
        assert ids == [f"{digest}.0", f"{digest}.1"]

    def test_open_blocks_are_skipped(self):
        assert block_ids("<ui><text>x</text>") == []

    def test_hash_matches_the_split_source_from_the_shared_fixture(self):
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["split"]
        for case in cases:
            closed = [s for s in case["segments"] if s["kind"] == "ui" and s["closed"]]
            assert [s for _, s in block_ids(case["text"])] == [
                s["source"] for s in closed
            ], case["name"]


class TestSubmit:
    async def test_accepts_declared_keys_and_broadcasts_to_other_outputs(
        self, tmp_path
    ):
        store = _store(tmp_path)
        service, out, other = _service(store)
        status = service.submit(
            1, 1, _first_id(), {"people": 5, "ghost": 1}, source=out
        )
        await asyncio.sleep(0)
        assert status == "accepted"
        assert service.get(1, 1, _first_id()) == {"people": 5}
        assert out.widget_events == [], "the sender is excluded"
        (event,) = other.widget_events
        assert event.type == "inline_ui_state"
        assert event.payload == {
            "ui_turn": 1,
            "ui_branch": 1,
            "block": _first_id(),
            "state": {"people": 5},
        }
        service.submit(1, 1, _first_id(), {"note": "hi"})
        assert service.get(1, 1, _first_id()) == {"people": 5, "note": "hi"}
        store.close()

    async def test_unknown_turn_branch_block_or_shape(self, tmp_path):
        store = _store(tmp_path)
        service, _, other = _service(store)
        bid = _first_id()
        assert service.submit(2, 1, bid, {"people": 1}) == "unknown"
        assert service.submit(1, 2, bid, {"people": 1}) == "unknown"
        assert service.submit(1, 1, "deadbeef.0", {"people": 1}) == "unknown"
        assert service.submit(1, 1, "not-an-id", {"people": 1}) == "unknown"
        assert service.submit("1", 1, bid, {"people": 1}) == "unknown"
        assert service.submit(1, 1, bid, ["people"]) == "dropped"
        assert service.submit(1, 1, bid, {"ghost": 1}) == "dropped"
        await asyncio.sleep(0)
        assert other.widget_events == []
        store.close()

    async def test_second_identical_block_has_its_own_state(self, tmp_path):
        store = _store(tmp_path)
        service, _, _ = _service(store)
        first, second = (bid for bid, _ in block_ids(REPLY))
        service.submit(1, 1, first, {"people": 2})
        service.submit(1, 1, second, {"people": 7})
        assert service.get(1, 1, first) == {"people": 2}
        assert service.get(1, 1, second) == {"people": 7}
        store.close()

    async def test_a_regenerated_branch_starts_empty(self, tmp_path):
        store = _store(tmp_path)
        store.append_event(
            "creature",
            "assistant_message",
            {"message": {"role": "assistant", "content": REPLY}},
            turn_index=1,
            branch_id=2,
        )
        service, _, _ = _service(store)
        service.submit(1, 1, _first_id(), {"people": 4})
        assert service.get(1, 2, _first_id()) is None
        assert service.submit(1, 2, _first_id(), {"people": 6}) == "accepted"
        assert service.get(1, 1, _first_id()) == {"people": 4}
        store.close()

    async def test_ui_only_state_merges_per_node_path(self, tmp_path):
        store = _store(tmp_path)
        service, _, other = _service(store)
        bid = _first_id()
        sort = {"root.children[0]": {"sort": "price", "desc": True}}
        assert service.submit(1, 1, bid, {"__ui": sort}) == "accepted"
        tree = {"root.children[1]": {"open": ["src"]}}
        assert service.submit(1, 1, bid, {"people": 2, "__ui": tree}) == "accepted"
        assert service.get(1, 1, bid) == {"people": 2, "__ui": {**sort, **tree}}
        bad = {"has space": {}, "root": "not an object", 3: {}}
        assert service.submit(1, 1, bid, {"__ui": bad}) == "dropped"
        assert service.submit(1, 1, bid, {"__ui": "x"}) == "dropped"
        await asyncio.sleep(0)
        assert other.widget_events[-1].payload["state"]["__ui"] == {**sort, **tree}
        store.close()

    async def test_ui_only_state_respects_the_entry_and_size_caps(
        self, tmp_path, monkeypatch
    ):
        store = _store(tmp_path)
        service, _, _ = _service(store)
        bid = _first_id()
        monkeypatch.setattr(mod, "MAX_UI_ENTRIES", 2)
        many = {f"root.children[{i}]": {"open": True} for i in range(3)}
        assert service.submit(1, 1, bid, {"__ui": many}) == "dropped"
        monkeypatch.setattr(mod, "MAX_STATE_BYTES", 64)
        big = {"root": {"filter": "x" * 100}}
        assert service.submit(1, 1, bid, {"__ui": big}) == "dropped"
        assert service.get(1, 1, bid) is None
        store.close()

    async def test_size_cap(self, tmp_path, monkeypatch):
        store = _store(tmp_path)
        service, _, _ = _service(store)
        monkeypatch.setattr(mod, "MAX_STATE_BYTES", 64)
        assert service.submit(1, 1, _first_id(), {"note": "x" * 100}) == "dropped"
        assert service.get(1, 1, _first_id()) is None
        store.close()

    async def test_list_content_and_a_malformed_block(self, tmp_path):
        bad = "<ui><card><text>x</card></ui>"
        store = SessionStore(tmp_path / "s.kohakutr")
        store.append_event(
            "creature",
            "assistant_message",
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": REPLY + bad}],
                }
            },
            turn_index=3,
            branch_id=1,
        )
        service, _, _ = _service(store)
        assert service.submit(3, 1, _first_id(), {"people": 2}) == "accepted"
        bad_id = f"{block_hash(bad)}.0"
        assert service.submit(3, 1, bad_id, {"x": 1}) == "unknown"
        store.close()


class TestRestore:
    async def test_recorded_state_comes_back_latest_wins(self, tmp_path):
        store = _store(tmp_path)
        bid = _first_id()
        for people in (2, 8):
            store.append_event(
                "creature",
                "inline_ui_state",
                {
                    "ui_turn": 1,
                    "ui_branch": 1,
                    "block": bid,
                    "state": {"people": people},
                },
            )
        service, _, _ = _service(store)
        assert service.get(1, 1, bid) == {"people": 8}
        service.submit(1, 1, bid, {"note": "kept"})
        assert service.get(1, 1, bid) == {"people": 8, "note": "kept"}
        store.close()

    async def test_without_a_store_the_live_conversation_is_searched(self):
        class Message:
            role = "assistant"
            content = [TextPart(text="intro "), TextPart(text=REPLY)]

        class Conversation:
            def get_messages(self):
                return [Message()]

        class Controller:
            conversation = Conversation()

        agent = Agent()
        agent.controller = Controller()
        service = InlineUiStates(OutputRouter(Recorder()))
        service.bind_agent(agent)
        assert service.submit(9, 9, _first_id(), {"people": 3}) == "accepted"
        assert service.get(9, 9, _first_id()) == {"people": 3}
        unbound = InlineUiStates(OutputRouter(Recorder()))
        assert unbound.submit(1, 1, _first_id(), {"people": 3}) == "unknown"
