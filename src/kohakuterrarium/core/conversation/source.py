"""Detached source context for existing history mutations."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.core.conversation.branch import build_branch_conversation
from kohakuterrarium.core.conversation.conversation import Conversation
from kohakuterrarium.core.conversation.elide import (
    elide_stale_tool_results,
    estimate_tokens,
)
from kohakuterrarium.core.conversation.snapshot import replayed_compact_state


def copy_conversation(conversation: Conversation) -> Conversation:
    """Copy messages and metadata while retaining the runtime configuration."""
    result = deepcopy(conversation)
    result.config = conversation.config
    return result


@dataclass(frozen=True)
class PreparedHistorySource:
    """Operation-owned source before destination truncation or user retagging."""

    conversation: Conversation
    turn_index: int
    branch_id: int
    parent_path: tuple[tuple[int, int], ...]
    cutoff: int
    compact_state: dict[str, Any] | None

    @property
    def view(self) -> dict[int, int]:
        selected = dict(self.parent_path)
        if self.turn_index > 0 and self.branch_id > 0:
            selected[self.turn_index] = self.branch_id
        return selected

    @classmethod
    def capture(cls, agent: Any) -> "PreparedHistorySource":
        manager = getattr(agent, "compact_manager", None)
        return cls(
            copy_conversation(agent.controller.conversation),
            getattr(agent, "_turn_index", 0),
            getattr(agent, "_branch_id", 0),
            tuple(tuple(pair) for pair in getattr(agent, "_parent_branch_path", ())),
            getattr(agent, "_context_cutoff_event_id", 0),
            (
                {
                    "compact_count": getattr(manager, "_compact_count", 0),
                    "last_compact_time": getattr(manager, "_last_compact_time", 0.0),
                }
                if manager is not None
                else None
            ),
        )

    def adopt(self, agent: Any, *, preserve_identity: bool = True) -> Conversation:
        """Install a separate destination copy and the prepared history coordinates."""
        candidate = copy_conversation(self.conversation)
        if preserve_identity:
            agent.controller.conversation.adopt_contents(candidate)
        else:
            agent.controller.conversation = candidate
        agent._turn_index = self.turn_index
        agent._branch_id = self.branch_id
        agent._parent_branch_path = list(self.parent_path)
        agent._context_cutoff_event_id = self.cutoff
        manager = getattr(agent, "compact_manager", None)
        if manager is not None and self.compact_state is not None:
            manager._compact_count = self.compact_state["compact_count"]
            manager._last_compact_time = self.compact_state["last_compact_time"]
        return agent.controller.conversation


def prepare_branch_source(
    agent: Any, source: PreparedHistorySource, prepared: dict
) -> PreparedHistorySource:
    """Build a selected branch with existing retention and tool-elision rules."""
    candidate = build_branch_conversation(source.conversation, prepared["messages"])
    candidate = copy_conversation(candidate)
    config = getattr(agent.controller, "config", None)
    compact = getattr(agent, "compact_manager", None)
    if config is not None and getattr(config, "elide_tool_results", False):
        maximum = (
            compact.config.max_tokens
            if compact is not None
            and compact.config.enabled
            and getattr(compact.config, "max_tokens", 0)
            else 0
        )
        if maximum and estimate_tokens(candidate) >= int(
            maximum * compact.config.threshold
        ):
            elide_stale_tool_results(candidate)
    selected = prepared["selected"]
    turn = max(selected, default=0)
    return PreparedHistorySource(
        candidate,
        turn,
        selected.get(turn, 0),
        tuple((t, b) for t, b in sorted(selected.items()) if 0 < t < turn and b > 0),
        prepared.get("context_cutoff_event_id", 0),
        deepcopy(
            prepared.get("compact_state")
            or replayed_compact_state(prepared["messages"])
        ),
    )
