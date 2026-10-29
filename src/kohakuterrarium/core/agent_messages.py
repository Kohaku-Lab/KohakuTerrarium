"""Message edit / regenerate / rewind mixin for Agent.

Modify past messages, regenerate responses, and replay conversation branches.
"""

import asyncio

from kohakuterrarium.core.agent_lifecycle import wait_for_turn_completion
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
    prepare_raw_prefix_for_target,
    raw_target_content,
)
from kohakuterrarium.core.branch_continuation import (
    live_branch_path,
    persist_branch_view,
    require_no_active_compact,
    require_no_running_jobs,
)
from kohakuterrarium.core.compact_branch import (
    compact_source_path_from_event,
    is_summary_message,
)
from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.core.history_source import (
    PreparedHistorySource,
    prepare_branch_source,
)
from kohakuterrarium.core.settled_refresh import prepare_mutation_prefix
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.llm.message import content_parts_to_dicts, normalize_content_parts
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.snapshot import save_agent_snapshot
from kohakuterrarium.session.raw_history import (
    UserMessageSelector,
    append_user_event_pair,
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
                request_id=request_id,
            )
            return

        async with self._get_message_mutation_lock():
            rerun = await self._regenerate_tail_response(request_id=request_id)
        if rerun:
            try:
                await self._rerun_from_last()
            finally:
                self._branch_request_id = None

    def _prepare_changed_summary_inputs(
        self, source: PreparedHistorySource, turn_index: int | None, branch_id: int
    ) -> PreparedHistorySource:
        """Prepare a reconstructed source when a mutation changes summary inputs."""
        if not any(
            (inputs := compact_source_path_from_event(msg.metadata)) is None
            or turn_index is None
            or any(turn >= turn_index for turn, _ in inputs)
            for msg in source.conversation.get_messages()
            if is_summary_message(msg)
        ):
            return source
        if self.session_store is None or turn_index is None:
            raise ConflictError(
                "Cannot reconstruct summarized history without a persisted target"
            )
        targets = [
            evt
            for evt in self.session_store.get_events(self.config.name)
            if evt.get("type") == "user_message"
            and evt.get("turn_index") == turn_index
            and evt.get("branch_id") == branch_id
        ]
        if len(targets) != 1:
            raise ConflictError("Cannot identify the summarized turn to mutate")
        return prepare_raw_prefix_for_target(
            self,
            UserMessageSelector(targets[0]["event_id"], turn_index, branch_id),
            branch_view=source.view,
            source=source,
        )

    async def _regenerate_tail_response(self, *, request_id: str | None) -> bool:
        """Commit a sibling branch of the current tail, then return whether to rerun."""
        await self._wait_for_history_finalization()
        self._ensure_history_mutation_idle()
        self._ensure_rerun_available()
        source = PreparedHistorySource.capture(self)
        source = self._prepare_changed_summary_inputs(
            source, source.turn_index, source.branch_id
        )
        last_user = source.conversation.find_last_user_index()
        if last_user < 0:
            logger.warning("No user message to regenerate from")
            return False
        source, last_user = prepare_mutation_prefix(self, source, last_user)
        next_branch = self._max_branch_id_for_turn(source.turn_index) + 1
        prev_content = self._user_message_content_for_turn(
            source.turn_index, branch_view=source.view
        )
        conv = source.adopt(self)
        removed = conv.truncate_from(last_user + 1)
        self._branch_id = next_branch
        conv.get_messages()[last_user].metadata.update(
            turn_index=self._turn_index, branch_id=self._branch_id
        )
        logger.info(
            "Regenerating",
            dropped=len(removed),
            turn_index=self._turn_index,
            branch_id=self._branch_id,
        )
        if self.session_store is not None and prev_content is not None:
            ppath = [tuple(p) for p in getattr(self, "_parent_branch_path", [])]
            _, event_id = await self.session_store.run(
                append_user_event_pair,
                self.session_store,
                self.config.name,
                {
                    "content": prev_content,
                    **({"request_id": request_id} if request_id else {}),
                },
                self._turn_index,
                self._branch_id,
                ppath,
            )
            conv.get_messages()[last_user].metadata["event_id"] = event_id
        persist_branch_view(
            self.session_store, self.config.name, live_branch_path(self)
        )
        self._branch_request_id = request_id
        return True

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
        """Serialize conversation reconstruction; the rerun turn runs after the lock."""
        async with self._get_message_mutation_lock():
            prepared = await self._edit_and_rerun_locked(
                message_idx,
                new_content,
                turn_index=turn_index,
                user_position=user_position,
                branch_view=branch_view,
                request_id=request_id,
                target=target,
            )
        if prepared:
            try:
                await self._rerun_from_last(new_user_content=new_content)
            finally:
                self._branch_request_id = None
            return True
        return False

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
        """Prepare the selected source, replace its user target, and open a sibling.

        Accepts canonical targets, turn/user coordinates, or a legacy message index.
        """
        await self._wait_for_history_finalization()
        self._ensure_history_mutation_idle()
        self._ensure_rerun_available()
        source = PreparedHistorySource.capture(self)
        if target is not None:
            source = prepare_raw_prefix_for_target(
                self, target, branch_view=branch_view, source=source
            )
            turn_index = target.turn_index
            user_position = None
        elif branch_view and self.session_store is not None:
            prepared = self._reload_conversation_under_branch_view(
                branch_view, apply=False
            )
            source = prepare_branch_source(self, source, prepared)

        conv = source.conversation
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
        source, resolved_idx = prepare_mutation_prefix(self, source, resolved_idx)
        msgs = source.conversation.get_messages()
        target = msgs[resolved_idx]
        if target.role != "user":
            logger.warning("Can only edit user messages", role=target.role)
            return False
        # Compute the user-message position so we can map back to a
        # turn_index in the event log.
        resolved_user_position = (
            sum(1 for m in msgs[: resolved_idx + 1] if m.role == "user") - 1
        )
        resolved_turn = turn_index or target.metadata.get("turn_index")
        if resolved_turn is None:
            if any(is_summary_message(msg) for msg in msgs):
                raise ConflictError(
                    "Cannot identify an untagged target in summarized history"
                )
            resolved_turn = self._turn_index_for_user_position(
                resolved_user_position, branch_view=branch_view
            )
        resolved_branch = target.metadata.get("branch_id") or (branch_view or {}).get(
            resolved_turn, source.branch_id
        )
        reconstructed = self._prepare_changed_summary_inputs(
            source, resolved_turn, resolved_branch
        )
        if reconstructed is not source:
            resolved_idx = reconstructed.conversation.find_last_user_index()
            if resolved_idx < 0:
                raise ConflictError("Prepared history has no mutation target")
            source, resolved_idx = prepare_mutation_prefix(
                self, reconstructed, resolved_idx
            )
        turn_index = resolved_turn
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
            target_turn_index = source.turn_index if source.turn_index > 0 else None
        next_branch = (
            self._max_branch_id_for_turn(target_turn_index) + 1
            if target_turn_index is not None and self.session_store is not None
            else max(source.branch_id, 1) + 1
        )
        conv = source.adopt(self)
        conv.truncate_from(resolved_idx)
        if target_turn_index is not None:
            self._turn_index = target_turn_index
        self._branch_id = next_branch
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
            await self.session_store.run(
                append_user_event_pair,
                self.session_store,
                self.config.name,
                {
                    "content": (
                        content_parts_to_dicts(new_content)
                        if isinstance(new_content, list)
                        else new_content
                    ),
                    **({"request_id": request_id} if request_id else {}),
                },
                self._turn_index,
                self._branch_id,
                ppath,
            )
        persist_branch_view(
            self.session_store, self.config.name, live_branch_path(self)
        )
        self._branch_request_id = request_id
        return True

    async def rewind_to(self, message_idx: int) -> None:
        """Drop messages from ``message_idx`` onward without re-running."""
        async with self._get_message_mutation_lock():
            await self._wait_for_history_finalization()
            self._ensure_history_mutation_idle()
            conv = self.controller.conversation
            removed = conv.truncate_from(message_idx)
            logger.info("Rewound", index=message_idx, dropped=len(removed))
            if self.session_store:
                try:
                    save_agent_snapshot(
                        self.session_store,
                        self.config.name,
                        self,
                        conv.snapshot_messages(),
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

    async def _wait_for_history_finalization(self) -> None:
        """Wait for cancellation or finalization, never for an actively generating turn."""
        if (
            getattr(self, "_interrupt_requested", False)
            or getattr(self, "_processing_task", None) is None
        ):
            await wait_for_turn_completion(
                self, getattr(self, "_turn_completion", None)
            )

    def _ensure_history_mutation_idle(self) -> None:
        """Reject destructive history changes while another turn can observe it."""
        require_no_active_compact(self)
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
        require_no_running_jobs(self)

    async def _rerun_from_last(self, new_user_content: str | list = "") -> None:
        """Trigger a regeneration or edit turn with normalized content parts."""
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
        """Return the largest persisted branch ID for a turn, or zero."""
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
        *,
        events=None,
        apply: bool = True,
    ):
        """Replay ``branch_view`` and optionally install it on the agent."""
        return _reload_branch_view(
            self,
            branch_view,
            replay=replay_conversation,
            events=events,
            apply=apply,
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
