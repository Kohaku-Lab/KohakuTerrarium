"""Message edit / regenerate / rewind mixin for Agent.

Core feature: modify past messages and re-run the turn. Works from
TUI, frontend, and programmatic API — all three call the same
implementation.
"""

from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentMessagesMixin:
    """Message edit / regenerate / rewind operations."""

    async def regenerate_last_response(self) -> None:
        """Pop the last assistant message (+tool results) and re-run LLM.

        Uses current model/settings — which may differ from when the
        original response was generated. Opens a new ``branch_id`` for
        the current ``turn_index`` so the original branch is preserved
        and addressable via the ``<1/N>`` navigator.
        """
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
        await self._rerun_from_last()

    async def edit_and_rerun(
        self,
        message_idx: int,
        new_content: str,
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
    ) -> bool:
        """Replace a user message and re-run from there.

        ``message_idx`` remains the raw in-memory conversation index for
        CLI/back-compat callers. Frontend callers should pass a stable
        ``turn_index`` or visible ``user_position`` so system/tool
        messages cannot shift the target.
        """
        conv = self.controller.conversation
        msgs = conv.get_messages()
        resolved_idx = self._resolve_edit_message_index(
            msgs, message_idx, turn_index=turn_index, user_position=user_position
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
                resolved_user_position
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
        # carrying the edited content. agent_handlers will skip its
        # own append because ``rerun`` is set on the trigger.
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
        await self._rerun_from_last(new_user_content=new_content)
        return True

    async def rewind_to(self, message_idx: int) -> None:
        """Drop messages from ``message_idx`` onward without re-running."""
        conv = self.controller.conversation
        removed = conv.truncate_from(message_idx)
        logger.info("Rewound", index=message_idx, dropped=len(removed))
        if self.session_store:
            try:
                self.session_store.save_conversation(
                    self.config.name, conv.to_messages()
                )
            except Exception as e:
                logger.debug(
                    "Failed to save conversation after rewind",
                    error=str(e),
                    exc_info=True,
                )

    async def _rerun_from_last(self, new_user_content: str = "") -> None:
        """Trigger a new LLM turn from the current conversation state.

        ``new_user_content`` is empty for plain regenerate (no new
        user message — we are re-running with the existing one) and
        non-empty for edit+rerun (the controller and event log need
        to record the edited content).
        """
        edited = bool(new_user_content)
        event = TriggerEvent(
            type=EventType.USER_INPUT,
            content=new_user_content,
            context={"rerun": True, "edited": edited},
            stackable=False,
        )
        await self._process_event(event)

    # ------------------------------------------------------------------
    # Branch resolution helpers
    # ------------------------------------------------------------------

    def _resolve_edit_message_index(
        self,
        msgs: list[object],
        message_idx: int,
        *,
        turn_index: int | None = None,
        user_position: int | None = None,
    ) -> int | None:
        """Resolve an edit target to an in-memory user-message index."""
        if turn_index is not None:
            pos = self._user_position_for_turn_index(turn_index)
            if pos is not None:
                user_position = pos
            elif user_position is None:
                return None
        if user_position is not None:
            if user_position < 0:
                return None
            seen = -1
            for idx, msg in enumerate(msgs):
                if msg.role != "user":
                    continue
                seen += 1
                if seen == user_position:
                    return idx
            return None
        if message_idx < 0 or message_idx >= len(msgs):
            return None
        return message_idx

    def _user_position_for_turn_index(self, turn_index: int) -> int | None:
        """Return the visible user-position for a live turn_index."""
        for pos, ti in enumerate(self._live_user_turns()):
            if ti == turn_index:
                return pos
        return None

    def _live_user_turns(self) -> list[int]:
        """Return live user turn_index values in replay order."""
        if self.session_store is None:
            return []
        try:
            events = self.session_store.get_events(self.config.name)
        except Exception as e:
            logger.debug("Failed to read events for live turns", error=str(e))
            return []
        latest_branch: dict[int, int] = {}
        for evt in events:
            ti = evt.get("turn_index")
            bi = evt.get("branch_id")
            if isinstance(ti, int) and isinstance(bi, int):
                if bi > latest_branch.get(ti, 0):
                    latest_branch[ti] = bi
        live_user_turns: list[int] = []
        for evt in events:
            if evt.get("type") != "user_message":
                continue
            ti = evt.get("turn_index")
            bi = evt.get("branch_id")
            if not isinstance(ti, int) or not isinstance(bi, int):
                continue
            if bi != latest_branch.get(ti):
                continue
            live_user_turns.append(ti)
        return live_user_turns

    def _turn_index_for_user_position(self, user_position: int) -> int | None:
        """Return the ``turn_index`` of the ``user_position``-th live
        user_message event, or ``None`` if it cannot be resolved.

        Live = belonging to the latest branch of its turn. We walk
        events grouping by ``turn_index``, picking the latest
        ``branch_id`` per turn, then scan the resulting user_message
        events in order.
        """
        live_user_turns = self._live_user_turns()
        if user_position < 0 or user_position >= len(live_user_turns):
            return None
        return live_user_turns[user_position]

    def _max_branch_id_for_turn(self, turn_index: int) -> int:
        """Return the largest ``branch_id`` recorded for ``turn_index``,
        or ``0`` if no branch yet exists."""
        if self.session_store is None:
            return 0
        try:
            events = self.session_store.get_events(self.config.name)
        except Exception as e:
            logger.debug("Failed to read events for branch lookup", error=str(e))
            return 0
        max_branch = 0
        for evt in events:
            if evt.get("turn_index") == turn_index:
                bi = evt.get("branch_id")
                if isinstance(bi, int) and bi > max_branch:
                    max_branch = bi
        return max_branch

    def _previous_branch_user_content(self):
        """Return the ``user_message`` content recorded for the most
        recent prior branch of ``self._turn_index``, or ``None`` if no
        such event is found.

        Used by ``regenerate_last_response`` to seed the new branch's
        ``user_message`` event with the same wording as the original
        branch (pure regen does not change the user message).
        """
        if self.session_store is None:
            return None
        try:
            events = self.session_store.get_events(self.config.name)
        except Exception as e:
            logger.debug("Failed to read events for prev-branch user", error=str(e))
            return None
        latest_for_turn: dict | None = None
        latest_branch = -1
        for evt in events:
            if evt.get("type") != "user_message":
                continue
            if evt.get("turn_index") != self._turn_index:
                continue
            bi = evt.get("branch_id")
            if not isinstance(bi, int):
                continue
            # We want the highest branch_id that is BELOW the current
            # branch we are about to write — that's the one to copy
            # the wording from.
            if bi < self._branch_id and bi > latest_branch:
                latest_branch = bi
                latest_for_turn = evt
        if latest_for_turn is None:
            return None
        return latest_for_turn.get("content")
