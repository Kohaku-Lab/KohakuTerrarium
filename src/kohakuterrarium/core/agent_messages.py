"""Message edit / regenerate / rewind mixin for Agent.

Core feature: modify past messages and re-run the turn. Works from
TUI, frontend, and programmatic API — all three call the same
implementation.
"""

from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.llm.message import normalize_content_parts
from kohakuterrarium.session.history import (
    _index_parent_paths,
    _resolve_selected_branches,
    replay_conversation,
    select_live_event_ids,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentMessagesMixin:
    """Message edit / regenerate / rewind operations."""

    async def regenerate_last_response(
        self,
        *,
        turn_index: int | None = None,
        branch_view: dict[int, int] | None = None,
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
        if turn_index is not None:
            # Resolve the user_message content for this turn at the
            # selected branch in our subtree, then route through
            # edit_and_rerun with that same content — semantically
            # "edit to identical content," which opens a new branch
            # at the requested turn just like a tail regen would for
            # the last turn.
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
        branch_view: dict[int, int] | None = None,
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
        # Reload conversation under the chosen subtree FIRST so the
        # in-memory message list reflects what the user sees in the UI.
        # Without this, edits on a non-latest branch silently fail
        # because the agent's in-memory state is on a different branch.
        if branch_view:
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

    async def _rerun_from_last(self, new_user_content: str | list = "") -> None:
        """Trigger a new LLM turn from the current conversation state.

        ``new_user_content`` is empty for plain regenerate (no new
        user message — we are re-running with the existing one) and
        non-empty for edit+rerun (the controller and event log need
        to record the edited content).

        Multi-modal callers (frontend ``editMessage`` builds a list of
        ``{type, text|image_url|file}`` dicts via ``buildMessageParts``)
        must be normalised to ``ContentPart`` instances before the
        TriggerEvent reaches ``_format_events_for_context`` — that
        helper only matches ``TextPart`` / ``ImagePart`` / ``FilePart``
        objects. A raw dict-list silently produces empty
        ``combined_text``, which in native mode collapses to
        ``skip_empty=True`` and the LLM ends up running with no new
        user message at all (the symptom users saw: "edit + rerun
        runs without the edit").
        """
        edited = bool(new_user_content)
        normalised = normalize_content_parts(new_user_content)
        if normalised is None:
            normalised = new_user_content if isinstance(new_user_content, str) else ""
        event = TriggerEvent(
            type=EventType.USER_INPUT,
            content=normalised,
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
        branch_view: dict[int, int] | None = None,
    ) -> int | None:
        """Resolve an edit target to an in-memory user-message index."""
        if turn_index is not None:
            pos = self._user_position_for_turn_index(
                turn_index, branch_view=branch_view
            )
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

    def _user_position_for_turn_index(
        self,
        turn_index: int,
        *,
        branch_view: dict[int, int] | None = None,
    ) -> int | None:
        """Return the visible user-position for a live turn_index."""
        for pos, ti in enumerate(self._live_user_turns(branch_view=branch_view)):
            if ti == turn_index:
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
        if self.session_store is None:
            return []
        try:
            events = self.session_store.get_events(self.config.name)
        except Exception as e:
            logger.debug("Failed to read events for live turns", error=str(e))
            return []
        live_ids = select_live_event_ids(events, branch_view=branch_view)
        seen_turns: set[int] = set()
        live_user_turns: list[tuple[int, int]] = []
        for evt in events:
            if evt.get("type") != "user_message":
                continue
            eid = evt.get("event_id")
            ti = evt.get("turn_index")
            if not isinstance(eid, int) or not isinstance(ti, int):
                continue
            if eid not in live_ids:
                continue
            if ti in seen_turns:
                # Tolerate legacy sessions that accumulated duplicates
                # from the pre-fix double-append bug.
                continue
            seen_turns.add(ti)
            live_user_turns.append((ti, eid))
        # Sort by turn_index so position N maps to the Nth visible turn,
        # not the Nth event in chronological order (which can scramble
        # when sibling branches interleave in the event log).
        live_user_turns.sort(key=lambda p: p[0])
        return [ti for ti, _ in live_user_turns]

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
        live_user_turns = self._live_user_turns(branch_view=branch_view)
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
        if self.session_store is None:
            return None
        try:
            events = self.session_store.get_events(self.config.name)
        except Exception as e:
            logger.debug(
                "Failed to read events for turn-content lookup",
                error=str(e),
            )
            return None
        parent_paths = _index_parent_paths(events)
        selected = _resolve_selected_branches(events, parent_paths, branch_view)
        target_branch = selected.get(turn_index)
        if target_branch is None:
            return None
        for evt in events:
            if evt.get("type") != "user_message":
                continue
            if evt.get("turn_index") != turn_index:
                continue
            if evt.get("branch_id") != target_branch:
                continue
            return evt.get("content")
        return None

    def _reload_conversation_under_branch_view(
        self,
        branch_view: dict[int, int],
    ) -> None:
        """Replay events under ``branch_view`` and reset in-memory
        conversation + agent state to match.

        Frontend ``selectBranch`` is a view-only operation; the
        agent's runtime state stays on whatever branch it last ran.
        When the user then triggers edit/retry on the switched view,
        the runtime state must match the user's view before truncate
        + rerun, or the resolution lands on the wrong message and
        the edit silently fails (the "can't edit on old branch" bug).
        """
        if self.session_store is None:
            return
        try:
            events = self.session_store.get_events(self.config.name)
        except Exception as e:
            logger.debug(
                "Failed to read events for branch_view reload",
                error=str(e),
            )
            return

        # Compute the chosen subtree's leaf state up front so we can
        # set agent metadata after reseating the conversation.
        parent_paths = _index_parent_paths(events)
        selected = _resolve_selected_branches(events, parent_paths, branch_view)

        messages = replay_conversation(events, branch_view=branch_view)
        conv = self.controller.conversation
        # Keep the system prompt (it carries tool docs and agent
        # personality). ``replay_conversation`` does not emit system
        # messages from the event log, so we preserve whatever the
        # controller set up at boot.
        existing_system = [m for m in conv.get_messages() if m.role == "system"]
        conv._messages.clear()
        conv._messages.extend(existing_system)
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue
            content = msg.get("content", "")
            extra: dict = {}
            if msg.get("tool_calls"):
                extra["tool_calls"] = msg["tool_calls"]
            if msg.get("tool_call_id"):
                extra["tool_call_id"] = msg["tool_call_id"]
            if msg.get("name"):
                extra["name"] = msg["name"]
            conv.append(role, content, **extra)

        # Reseat agent state to the chosen subtree's leaf so the next
        # operation (edit/retry/continue) operates within this view.
        if selected:
            max_turn = max(selected.keys())
            self._turn_index = max_turn
            self._branch_id = selected[max_turn]
            self._parent_branch_path = [
                (t, b) for t, b in sorted(selected.items()) if t < max_turn
            ]
        else:
            self._turn_index = 0
            self._branch_id = 0
            self._parent_branch_path = []

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
