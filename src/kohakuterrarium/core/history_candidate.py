"""Admission checks and detached installation for history-mutation candidates.

Everything here guards *reconstructed* prompts only: the prefix an edit, rerun
or raw-history restore is about to install, together with the new input that
will replace the mutation target. Normal continuation turns never come through
this module — they append their own input and must not be gated on the size of
a history rebuild they did not perform.
"""

from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.core.conversation import Conversation, ConversationConfig
from kohakuterrarium.core.conversation_elide import estimate_messages_tokens
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.llm.message import create_message


@dataclass(slots=True)
class HistoryCandidate:
    """Reconstructed messages and their branch, cutoff, and compaction state."""

    messages: list[Any]
    turn_index: int = 0
    branch_id: int = 0
    parent_branch_path: list[tuple[int, int]] = field(default_factory=list)
    compact_state: dict[str, Any] | None = None
    context_cutoff_event_id: int = 0
    target_index: int | None = None


def _role(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("role")
    return getattr(message, "role", None)


def _retention_overflow(
    config: Any, messages: list[Any]
) -> tuple[int, int, int] | None:
    """Return ``(kept non-system, non-system budget, limit)`` when appending drops.

    Mirrors :meth:`Conversation._maybe_truncate`: system prompts never consume
    the non-system budget, so a reconstruction is only admitted when none of its
    messages would be silently trimmed away.
    """
    limit = getattr(config, "max_messages", 0)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return None
    system = 0
    if getattr(config, "keep_system", True):
        system = sum(1 for message in messages if _role(message) == "system")
    others = len(messages) - system
    budget = limit - system
    if others > budget:
        return others, budget, limit
    return None


def require_history_candidate(agent: Any, messages: list[Any]) -> None:
    """Reject a reconstructed prompt that would be trimmed or overrun the budget.

    Callers pass only *reconstructed* prompts. A rebuild that would silently
    drop retained messages, or that no longer fits the model's context budget,
    is refused. Plain turns reusing the live conversation never come here, and
    the ceiling is the context budget itself (``max_tokens``), not the
    auto-compaction trigger fraction. The token figure is a conservative
    **text** estimate derived from character counts (see
    ``conversation_elide.CHARS_PER_TOKEN``), so it is a safety rail rather than
    a provider-exact count.
    """
    overflow = _retention_overflow(agent.controller.conversation.config, messages)
    if overflow is not None:
        kept, budget, limit = overflow
        raise ConflictError(
            "History reconstruction would discard prefix messages under the current "
            f"retention limit ({kept} non-system messages, budget {budget} of "
            f"limit {limit}). The original branch was not changed; select a "
            "compatible checkpoint or a later target."
        )
    compact = getattr(agent, "compact_manager", None)
    config = getattr(compact, "config", None)
    maximum = getattr(config, "max_tokens", 0)
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, (int, float))
        or maximum <= 0
    ):
        return
    limit = max(1, int(maximum))
    estimate = estimate_messages_tokens(messages)
    if estimate >= limit:
        raise ConflictError(
            "History reconstruction exceeds the configured context budget "
            f"(~{estimate} text tokens, limit {limit}; {len(messages)} messages). "
            "The budget is an approximate message-text estimate, not a "
            "provider-exact count. The original branch was not changed. "
            "Select a compatible checkpoint or a later target; no history was dropped."
        )


def final_prompt_messages(
    messages: list[Any], index: int, replacement: Any
) -> list[Any]:
    """Clone a detached prompt with the mutation target replaced by its new input.

    The clone keeps the old target (which may be far larger than the edit) out
    of the admission arithmetic: only the payload that will actually reach the
    provider is measured.
    """
    final = list(messages[: index + 1])
    final[index] = create_message("user", replacement)
    return final


def without_retention_config(config: Any) -> ConversationConfig:
    """Config clone that keeps every reconstructed message while assembling.

    Candidate assembly must never trim: trimming is the failure the admission
    check exists to prevent, and a dropped message would be an unrecoverable
    silent history loss.
    """
    return ConversationConfig(
        max_messages=0,
        keep_system=getattr(config, "keep_system", True),
        sanitize_orphan_tool_calls=getattr(config, "sanitize_orphan_tool_calls", True),
    )


def without_retention(current: Conversation) -> Conversation:
    """Detached copy of ``current`` that keeps all messages, for replay bases."""
    relaxed = Conversation(without_retention_config(current.config))
    relaxed._messages.extend(current.get_messages())
    relaxed._metadata.created_at = current._metadata.created_at
    relaxed._metadata.updated_at = current._metadata.updated_at
    relaxed._metadata.message_count = len(relaxed._messages)
    relaxed._metadata.total_chars = relaxed.get_context_length()
    return relaxed


def detached_conversation(current: Conversation, messages: list[Any]) -> Conversation:
    """Assemble a candidate conversation from messages without trimming."""
    candidate = Conversation(without_retention_config(current.config))
    for message in messages:
        candidate.append_message(message)
    candidate._metadata.created_at = current._metadata.created_at
    return candidate


def adopt_candidate_conversation(agent: Any, candidate: Conversation) -> None:
    """Install admitted messages while keeping the live conversation identity."""
    agent.controller.conversation.adopt_contents(candidate)


def install_candidate_state(agent: Any, candidate: HistoryCandidate) -> None:
    """Apply the non-message agent state belonging to an admitted candidate."""
    agent._context_cutoff_event_id = candidate.context_cutoff_event_id
    manager = getattr(agent, "compact_manager", None)
    if manager is not None and candidate.compact_state is not None:
        manager._compact_count = candidate.compact_state["compact_count"]
        manager._last_compact_time = candidate.compact_state["last_compact_time"]
    agent._turn_index = candidate.turn_index
    agent._branch_id = candidate.branch_id
    agent._parent_branch_path = [tuple(pair) for pair in candidate.parent_branch_path]
