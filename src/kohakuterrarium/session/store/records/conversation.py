"""Conversation snapshots, per-agent runtime state and turn rollups."""

import json
from typing import Any

from kohakuterrarium.session.store.records.rollup import (
    get_turn_rollup,
    list_turn_rollups,
    save_turn_rollup,
)
from kohakuterrarium.session.store.records.snapshot import (
    load_branch_snapshot,
    save_branch_snapshot,
)


class ConversationRecordsMixin:
    """Persist what a resumed agent needs besides its event log."""

    # Tables opened by the handle part.
    state: Any
    conversation: Any
    turn_rollup: Any

    def save_conversation(self, agent: str, messages: list[dict] | str) -> None:
        """Save a conversation snapshot (overwritten each time).

        Accepts either a list of message dicts (preferred, stored via msgpack)
        or a JSON string (legacy, stored as-is).
        """
        self.state[f"{agent}:branch_snapshots"] = []
        self.conversation[agent] = messages

    def save_branch_snapshot(
        self,
        agent: str,
        messages: list[dict],
        *,
        branch: dict | None,
        watermark: int,
        compact_state: dict | None = None,
    ) -> None:
        """Save a branch-local checkpoint alongside the latest conversation."""
        save_branch_snapshot(
            self,
            agent,
            messages,
            branch=branch,
            watermark=watermark,
            compact_state=compact_state,
        )

    def load_branch_snapshot(
        self, agent: str, branch_view: dict[int, int]
    ) -> dict | None:
        """Load a detached checkpoint on a resolved historical lineage."""
        return load_branch_snapshot(self, agent, branch_view)

    def load_conversation(self, agent: str) -> list[dict] | None:
        """Load the latest conversation snapshot for an agent.

        Returns a list of message dicts (OpenAI format), or None if not found.
        """
        try:
            val = self.conversation[agent]
            if isinstance(val, list):
                return val
            # Older sessions may contain JSON rather than auto-packed lists.
            if isinstance(val, (str, bytes)):
                s = (
                    val.decode("utf-8", errors="replace")
                    if isinstance(val, bytes)
                    else val
                )
                data = json.loads(s)
                if isinstance(data, dict) and "messages" in data:
                    return data["messages"]
                if isinstance(data, list):
                    return data
            return None
        except KeyError:
            return None

    def save_state(
        self,
        agent: str,
        *,
        scratchpad: dict[str, str] | None = None,
        turn_count: int | None = None,
        token_usage: dict[str, int] | None = None,
        triggers: list[dict] | None = None,
        compact_count: int | None = None,
    ) -> None:
        """Save per-agent runtime state."""
        if scratchpad is not None:
            self.state[f"{agent}:scratchpad"] = scratchpad
        if turn_count is not None:
            self.state[f"{agent}:turn_count"] = turn_count
        if token_usage is not None:
            self.state[f"{agent}:token_usage"] = token_usage
        if triggers is not None:
            self.state[f"{agent}:triggers"] = triggers
        if compact_count is not None:
            self.state[f"{agent}:compact_count"] = compact_count

    def load_scratchpad(self, agent: str) -> dict[str, str]:
        """Load scratchpad for an agent."""
        try:
            val = self.state[f"{agent}:scratchpad"]
            return val if isinstance(val, dict) else {}
        except KeyError:
            return {}

    def load_turn_count(self, agent: str) -> int:
        """Load turn count for an agent."""
        try:
            return int(self.state[f"{agent}:turn_count"])
        except (KeyError, TypeError, ValueError):
            return 0

    def load_token_usage(self, agent: str) -> dict[str, int]:
        """Load cumulative token usage for an agent."""
        try:
            val = self.state[f"{agent}:token_usage"]
            return val if isinstance(val, dict) else {}
        except KeyError:
            return {}

    def load_triggers(self, agent: str) -> list[dict]:
        """Load saved resumable triggers for an agent."""
        try:
            val = self.state[f"{agent}:triggers"]
            return val if isinstance(val, list) else []
        except KeyError:
            return []

    def save_turn_rollup(
        self, agent: str, turn_index: int, data: dict[str, Any]
    ) -> None:
        """Write a per-turn rollup row. See :mod:`session.rollup`."""
        save_turn_rollup(self.turn_rollup, agent, turn_index, data)

    def get_turn_rollup(self, agent: str, turn_index: int) -> dict | None:
        """Load a per-turn rollup row. Returns ``None`` when missing."""
        return get_turn_rollup(self.turn_rollup, agent, turn_index)

    def list_turn_rollups(self, agent: str) -> list[dict]:
        """List rollup rows for an agent, ordered by ``turn_index``."""
        return list_turn_rollups(self.turn_rollup, agent)
