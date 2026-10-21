"""Unit tests for the mid-turn append path in :mod:`core.agent_mid_turn`.

Folded events become one ``role=user`` message appended straight onto the
live conversation (they bypass ``Controller.run_once``), so the message has
to carry its own provenance: the active branch position plus the issuing job
ids of any folded result. Compaction resolves those ids through
:func:`job_origin_pairs`; anything it cannot resolve must stay unknown.
"""

from kohakuterrarium.core.agent_event_loop import AgentEventLoopMixin
from kohakuterrarium.core.agent_mid_turn import AgentMidTurnMixin
from kohakuterrarium.core.compact_branch import job_origin_pairs, summary_source_path
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.event_inbox import EventEnvelope, EventInbox
from kohakuterrarium.core.message_locator import user_message_indices_for_turn
from kohakuterrarium.core.events import (
    TriggerEvent,
    create_tool_complete_event,
    create_user_input_event,
)
from kohakuterrarium.llm.message import create_message


class _Router:
    def __init__(self) -> None:
        self.activities: list[tuple[str, dict]] = []

    def notify_activity(self, kind, detail, metadata=None) -> None:
        self.activities.append((kind, dict(metadata or {})))

    def add_secondary(self, capture) -> None:  # pragma: no cover - unused
        raise AssertionError("no capture expected")


class _Controller:
    def __init__(self) -> None:
        self.conversation = Conversation()


class _Agent(AgentMidTurnMixin, AgentEventLoopMixin):
    """Bare mixin host: no LLM, no store, active turn already at (5, 2)."""

    _turn_index = 5
    _branch_id = 2

    def __init__(self, events: list[TriggerEvent]) -> None:
        self._event_inbox = EventInbox()
        for event in events:
            self._event_inbox.put(EventEnvelope(event))
        self.output_router = _Router()
        self.session_store = None
        self._parent_branch_path = []


def _events_for(*job_ids: str) -> list[dict]:
    return [
        {
            "type": "tool_call",
            "call_id": job_id,
            "job_id": job_id,
            "turn_index": 1,
            "branch_id": 2,
        }
        for job_id in job_ids
    ]


async def _fold(events: list[TriggerEvent]):
    agent = _Agent(events)
    controller = _Controller()
    drained = await agent._drain_mid_turn_pending_inputs(controller)
    assert drained == len(events)
    return controller.conversation.get_messages()


async def test_folded_background_result_resolves_issuing_job():
    messages = await _fold([create_tool_complete_event("bg", "done")])

    assert len(messages) == 1
    metadata = messages[0].metadata
    assert metadata["turn_index"] == 5
    assert metadata["branch_id"] == 2
    assert metadata["origin_known"] is True
    assert metadata["job_ids"] == ["bg"]
    assert summary_source_path(
        messages, origins=job_origin_pairs(_events_for("bg"))
    ) == {
        (5, 2),
        (1, 2),
    }


async def test_folded_input_does_not_duplicate_the_canonical_turn_target():
    messages = await _fold([create_user_input_event("typed while busy")])
    root = create_message(
        "user", "original turn", metadata={"turn_index": 5, "branch_id": 2}
    )
    assert user_message_indices_for_turn([root, *messages], 5) == [0]
    assert messages[0].metadata["kind"] == "injected"


async def test_folded_user_input_keeps_active_position():
    messages = await _fold([create_user_input_event("typed while busy")])

    metadata = messages[0].metadata
    assert metadata["origin_known"] is True
    assert "job_ids" not in metadata
    assert summary_source_path(messages) == {(5, 2)}


async def test_folded_mix_with_unnamed_result_stays_unknown():
    messages = await _fold(
        [
            create_user_input_event("typed while busy"),
            TriggerEvent(type="tool_complete", content="wake"),
        ]
    )

    metadata = messages[0].metadata
    assert metadata["origin_known"] is False
    assert "job_ids" not in metadata
    assert summary_source_path(messages, origins=job_origin_pairs([])) is None


async def test_folded_mix_names_its_jobs():
    messages = await _fold(
        [
            create_user_input_event("typed while busy"),
            create_tool_complete_event("bg", "done"),
        ]
    )

    metadata = messages[0].metadata
    assert metadata["origin_known"] is True
    assert metadata["job_ids"] == ["bg"]
    assert summary_source_path(
        messages, origins=job_origin_pairs(_events_for("bg"))
    ) == {
        (5, 2),
        (1, 2),
    }
