"""Message edit / regenerate / rewind mixin for Agent.

Modify past messages, regenerate responses, and replay conversation branches.
"""

import asyncio

from kohakuterrarium.core.agent_message_history import (
    live_user_turns as _live_user_turns,
    max_branch_id_for_turn as _max_branch_id_for_turn,
    previous_branch_user_content as _previous_branch_user_content,
    reload_conversation_under_branch_view as _reload_branch_view,
    resolve_edit_message_index as _resolve_edit_message_index,
    turn_index_for_user_position as _turn_index_for_user_position,
    user_message_content_for_turn as _user_message_content_for_turn,
)
from kohakuterrarium.core.agent_raw_history import (
    raw_target_content,
    reload_raw_prefix_for_target,
)
from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.llm.message import normalize_content_parts
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.raw_history import UserMessageSelector
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentMessagesMixin:
    """Message edit / regenerate / rewind operations."""

    async def regenerate_last_response(
        self,
        *,
        turn_index: int | None = None,
        branch_view: dict[int, int] | None = None,
        request_id: str | None = None,
        target: UserMessageSelector | None = None,
    ) -> None:
        """Regenerate an assistant response.

        With ``turn_index=None`` (default): re-runs the conversation
        tail's last turn. With ``turn_index`` set: re-runs that
        specific turn (creates a new branch under the current
        viewing subtree). The latter is the path "click retry on an
        old assistant message" — without a turn_index parameter the
        backend always defaults to the tail and the click silently
        targets the wrong message.

        ``branch_view`` lets a caller retry on a NON-LATEST branch.
        Without it the agent's in-memory conversation reflects the
        last run (latest subtree) and a retry on an older branch
        would target the wrong message. Frontend passes the user's
        current ``branchViewByTab`` selection through.

        Uses current model/settings — which may differ from when the
        original response was generated. Opens a new ``branch_id`` for
        the resolved ``turn_index`` so the original branch is preserved
        and addressable via the ``<x/N>`` navigator.
        """
        if target is not None:
            await self.edit_and_rerun(
                message_idx=-1,
                new_content=raw_target_content(self, target, branch_view=branch_view),
                turn_index=target.turn_index,
                branch_view=branch_view,
                request_id=request_id,
                target=target,
            )
            return

        if turn_index is not None:
            # Reusing the selected branch's content gives regeneration the same
            # branching semantics as an edit without changing the message.
            prev_content = self._user_message_content_for_turn(
                turn_index, branch_view=branch_view
            )
            if prev_content is None:
                logger.warning(
                    "Cannot find user_message for turn",
                    turn=turn_index,
                )
                return
            user_position = self._user_position_for_turn_index(
                turn_index, branch_view=branch_view
            )
            if user_position is None:
                logger.warning(
                    "Cannot resolve user_position for turn",
                    turn=turn_index,
                )
                return
            await self.edit_and_rerun(
                message_idx=-1,
                new_content=prev_content,
                turn_index=turn_index,
                user_position=user_position,
                branch_view=branch_view,
            )
            return

        async with self._get_message_mutation_lock():
            await self._regenerate_tail_response(request_id=request_id)

    async def _regenerate_tail_response(self, *, request_id: str | None) -> None:
        """Regenerate the current tail while holding the mutation lock."""
        self._ensure_history_mutation_idle()
        self._ensure_rerun_available()
        conv = self.controller.conversation
        last_user = conv.find_last_user_index()
        if last_user < 0:
            logger.warning("No user message to regenerate from")
            return
        removed = conv.truncate_from(last_user + 1)
        # Open a new branch of the current turn.
        self._branch_id = self._max_branch_id_for_turn(self._turn_index) + 1
        logger.info(
            "Regenerating",
            dropped=len(removed),
            turn_index=self._turn_index,
            branch_id=self._branch_id,
        )
        # Emit fresh user_input + user_message events for the new
        # branch so replay (and the resume display surfaces that
        # group by ``user_input``) see a self-contained branch.
        # Pure regen mirrors the previous branch's wording — the
        # in-memory conversation already has the original user
        # message; the controller does NOT re-append on rerun.
        prev_content = self._previous_branch_user_content()
        if self.session_store is not None and prev_content is not None:
            # Pure regen keeps the existing parent path — we are
            # opening a sibling branch of the SAME turn, so the path
            # of prior turns is unchanged.
            ppath = [tuple(p) for p in getattr(self, "_parent_branch_path", [])]
            self.session_store.append_event(
                self.config.name,
                "user_input",
                {"content": prev_content},
                turn_index=self._turn_index,
                branch_id=self._branch_id,
                parent_branch_path=ppath,
            )
            self.session_store.append_event(
                self.config.name,
                "user_message",
                {"content": prev_content},
                turn_index=self._turn_index,
                branch_id=self._branch_id,
                parent_branch_path=ppath,
            )
        self._branch_request_id = request_id
        try:
            await self._rerun_from_last()
        finally:
            self._branch_request_id = None

    async def edit_and_rerun(
        self,
        message_idx: int,
        new_content: str,
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
        branch_view: dict[int, int] | None = None,
        request_id: str | None = None,
        target: UserMessageSelector | None = None,
    ) -> bool:
        """Serialize conversation reconstruction and the resulting rerun turn."""
        async with self._get_message_mutation_lock():
            return await self._edit_and_rerun_locked(
                message_idx,
                new_content,
                turn_index=turn_index,
                user_position=user_position,
                branch_view=branch_view,
                request_id=request_id,
                target=target,
            )

    async def _edit_and_rerun_locked(
        self,
        message_idx: int,
        new_content: str,
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
        branch_view: dict[int, int] | None = None,
        request_id: str | None = None,
        target: UserMessageSelector | None = None,
    ) -> bool:
        """Replace a user message and re-run from there.

        ``message_idx`` remains the raw in-memory conversation index for
        CLI/back-compat callers. Frontend callers should pass a stable
        ``turn_index`` or visible ``user_position`` so system/tool
        messages cannot shift the target.

        ``branch_view`` lets a caller edit on a NON-LATEST branch.
        When provided, the agent's in-memory conversation is replayed
        from events under the chosen view BEFORE the edit, so the
        truncation target resolves correctly even when the user has
        switched to an older subtree in the UI.
        """
        self._ensure_history_mutation_idle()
        self._ensure_rerun_available()
        # Canonical persisted targets reconstruct original context before
        # mutating in-memory state, deliberately bypassing compact snapshots.
        if target is not None:
            reload_raw_prefix_for_target(self, target, branch_view=branch_view)
            turn_index = target.turn_index
            user_position = None
        elif branch_view:
            self._reload_conversation_under_branch_view(branch_view)

        conv = self.controller.conversation
        msgs = conv.get_messages()
        resolved_idx = self._resolve_edit_message_index(
            msgs,
            message_idx,
            turn_index=turn_index,
            user_position=user_position,
            branch_view=branch_view,
        )
        if resolved_idx is None:
            logger.warning(
                "Invalid edit target",
                index=message_idx,
                turn_index=turn_index,
                user_position=user_position,
            )
            return False
        target = msgs[resolved_idx]
        if target.role != "user":
            logger.warning("Can only edit user messages", role=target.role)
            return False
        # Compute the user-message position so we can map back to a
        # turn_index in the event log.
        resolved_user_position = (
            sum(1 for m in msgs[: resolved_idx + 1] if m.role == "user") - 1
        )
        # Drop the old user message + everything after from the
        # in-memory conversation. Do NOT append the new user message
        # here — the rerun trigger carries it; the controller appends
        # it via ``_build_turn_context``.
        conv.truncate_from(resolved_idx)
        # Resolve the turn_index of the edited user message and bump
        # branch_id accordingly. If we cannot resolve it (no store, or
        # legacy events without turn_index), keep the agent's current
        # turn/branch state.
        target_turn_index = turn_index
        if target_turn_index is None:
            target_turn_index = self._turn_index_for_user_position(
                resolved_user_position,
                branch_view=branch_view,
            )
        if target_turn_index is None and user_position is not None:
            # No session/event metadata (common in narrow tests or
            # legacy in-memory agents). Position-based targeting still
            # found the right user message, so preserve old fallback
            # semantics and open a new branch on the current turn.
            target_turn_index = self._turn_index if self._turn_index > 0 else None
        if target_turn_index is not None:
            self._turn_index = target_turn_index
        self._branch_id = (
            self._max_branch_id_for_turn(self._turn_index) + 1
            if target_turn_index is not None and self.session_store is not None
            else max(self._branch_id, 1) + 1
        )
        logger.info(
            "Edited and re-running",
            index=resolved_idx,
            turn_index=self._turn_index,
            branch_id=self._branch_id,
        )
        # Emit user_input + user_message events for the new branch
        # carrying the edited content. ``_process_event`` in
        # ``agent_handlers`` skips its own append for rerun-flagged
        # triggers, so this is the authoritative writer for the new
        # branch's user-side events (we have the correct branch_id +
        # parent_branch_path computed already, which the handler
        # cannot replicate without re-reading the event log).
        # Edit+rerun on an EARLIER turn drops every later-turn entry
        # from the parent path — those follow-ups belong to a previous
        # subtree and the new edit forks from this point.
        cur_path = list(getattr(self, "_parent_branch_path", []))
        cur_path = [(t, b) for (t, b) in cur_path if t < self._turn_index]
        self._parent_branch_path = cur_path
        if self.session_store is not None:
            ppath = [tuple(p) for p in cur_path]
            self.session_store.append_event(
                self.config.name,
                "user_input",
                {"content": new_content},
                turn_index=self._turn_index,
                branch_id=self._branch_id,
                parent_branch_path=ppath,
            )
            self.session_store.append_event(
                self.config.name,
                "user_message",
                {"content": new_content},
                turn_index=self._turn_index,
                branch_id=self._branch_id,
                parent_branch_path=ppath,
            )
        self._branch_request_id = request_id
        try:
            await self._rerun_from_last(new_user_content=new_content)
        finally:
            self._branch_request_id = None
        return True

    async def rewind_to(self, message_idx: int) -> None:
        """Drop messages from ``message_idx`` onward without re-running."""
        async with self._get_message_mutation_lock():
            self._ensure_history_mutation_idle()
            conv = self.controller.conversation
            removed = conv.truncate_from(message_idx)
            logger.info("Rewound", index=message_idx, dropped=len(removed))
            if self.session_store:
                try:
                    self.session_store.save_conversation(
                        self.config.name, conv.snapshot_messages()
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to save conversation after rewind",
                        error=str(e),
                        exc_info=True,
                    )

    def _get_message_mutation_lock(self) -> asyncio.Lock:
        """Return the per-agent lock guarding destructive history mutations."""
        lock = getattr(self, "_message_mutation_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._message_mutation_lock = lock
        return lock

    def _ensure_history_mutation_idle(self) -> None:
        """Reject destructive history changes while another turn can observe it."""
        processing_lock = getattr(self, "_processing_lock", None)
        inbox = getattr(self, "_event_inbox", None)
        turn_active = processing_lock is not None and processing_lock.locked()
        input_pending = inbox is not None and len(inbox) > 0
        if turn_active or input_pending:
            raise ConflictError(
                "Cannot mutate conversation history while a turn is active"
            )

    def _ensure_rerun_available(self) -> None:
        """Reject reruns before mutation when the event loop cannot admit them."""
        if getattr(self, "_paused", False):
            raise ConflictError("Cannot rerun conversation while the agent is paused")
        if getattr(self, "_running", True) is False:
            raise ConflictError(
                "Cannot rerun conversation while the agent is not running"
            )

    async def _rerun_from_last(self, new_user_content: str | list = "") -> None:
        """Trigger an LLM turn from the current conversation state.

        Empty content regenerates from the existing user message; non-empty
        content records an edit. Normalization is required because downstream
        context formatting recognizes content-part objects rather than raw
        multimodal dictionaries.
        """
        edited = bool(new_user_content)
        normalised = normalize_content_parts(new_user_content)
        if normalised is None:
            normalised = new_user_content if isinstance(new_user_content, str) else ""
        event = TriggerEvent(
            type=EventType.USER_INPUT,
            content=normalised,
            context={
                "rerun": True,
                "edited": edited,
                "request_id": getattr(self, "_branch_request_id", None),
            },
            stackable=False,
        )
        await self._process_event(event)

    def _resolve_edit_message_index(
        self,
        msgs: list[object],
        message_idx: int,
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
        branch_view: dict[int, int] | None = None,
    ) -> int | None:
        """Resolve by turn metadata or exact unique legacy-content matching."""
        return _resolve_edit_message_index(
            self,
            msgs,
            message_idx,
            turn_index=turn_index,
            user_position=user_position,
            branch_view=branch_view,
        )

    def _user_position_for_turn_index(
        self,
        turn_index: int,
        *,
        branch_view: dict[int, int] | None = None,
    ) -> int | None:
        """Return the visible user-position for a live turn_index."""
        for pos, candidate in enumerate(self._live_user_turns(branch_view=branch_view)):
            if candidate == turn_index:
                return pos
        return None

    def _live_user_turns(
        self,
        *,
        branch_view: dict[int, int] | None = None,
    ) -> list[int]:
        """Return live user turn_index values in visible order.

        "Live" must match what the user actually SEES — the freshest
        subtree of the branch tree. Older subtrees that have been
        orphaned by a higher-up edit/retry must NOT contribute (their
        turns inflate positions and make ``_user_position_for_turn_index``
        resolve to the wrong message).

        Defers to ``select_live_event_ids`` from ``session/history.py``
        so this stays in lock-step with the replay logic.
        """
        return _live_user_turns(self, branch_view=branch_view)

    def _turn_index_for_user_position(
        self,
        user_position: int,
        *,
        branch_view: dict[int, int] | None = None,
    ) -> int | None:
        """Return the ``turn_index`` of the ``user_position``-th live
        user_message event, or ``None`` if it cannot be resolved.

        Live = belonging to the chosen branch of its turn under
        ``branch_view`` (or the latest subtree when ``branch_view``
        is ``None``).
        """
        return _turn_index_for_user_position(
            self,
            user_position,
            branch_view=branch_view,
        )

    def _max_branch_id_for_turn(self, turn_index: int) -> int:
        """Return the largest ``branch_id`` recorded for ``turn_index``,
        or ``0`` if no branch yet exists."""
        return _max_branch_id_for_turn(self, turn_index)

    def _user_message_content_for_turn(
        self,
        turn_index: int,
        *,
        branch_view: dict[int, int] | None = None,
    ):
        """Return the ``user_message`` content recorded at the chosen
        branch of ``turn_index`` under ``branch_view``, or ``None``.

        Used by ``regenerate_last_response(turn_index=…)`` so retry
        clicks on a non-tail turn carry the same content forward into
        the new branch — semantically equivalent to "edit to identical
        content." When ``branch_view`` is given the lookup respects
        the user's current subtree (otherwise it picks the latest
        branch globally).
        """
        return _user_message_content_for_turn(
            self,
            turn_index,
            branch_view=branch_view,
        )

    def _reload_conversation_under_branch_view(
        self,
        branch_view: dict[int, int],
    ) -> None:
        """Replay ``branch_view`` and align the conversation and agent state.

        Branch selection changes only the displayed view, so runtime state must
        be reseated before an edit or retry can resolve the intended message.
        """
        _reload_branch_view(
            self,
            branch_view,
            replay=replay_conversation,
        )

    def _previous_branch_user_content(self):
        """Return the ``user_message`` content recorded for the most
        recent prior branch of ``self._turn_index``, or ``None`` if no
        such event is found.

        Used by ``regenerate_last_response`` to seed the new branch's
        ``user_message`` event with the same wording as the original
        branch (pure regen does not change the user message).
        """
        return _previous_branch_user_content(self)
