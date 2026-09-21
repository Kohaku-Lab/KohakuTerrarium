"""Persist routed agent output and activity events to a session store.

Event appends are dispatched to the store's single-worker affinity thread in
submit order and become durable at the next :meth:`SessionOutput.drain`
boundary (turn end), so activity bursts never block the event loop. Readers
that go through the store's affinity thread observe submitted writes
automatically; the synchronous hooks keep their signatures for producers that
cannot await.
"""

import asyncio
from concurrent.futures import Future
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.output_activity import (
    SessionActivityMixin,
    _parse_detail,
    _subagent_name as _subagent_name,  # noqa: F401
    _token_metadata as _token_metadata,  # noqa: F401
)
from kohakuterrarium.session.text_buffer import (
    OpenTextSegment,
    last_persisted_turn_branch,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SessionOutput(SessionActivityMixin, OutputModule):
    """Output module that records events to a SessionStore.

    Streamed text is coalesced into one durable segment and flushed at the
    next non-text boundary. Interrupted segments retain their original turn.
    Activity is recorded immediately when enabled, and each processing cycle
    saves the conversation snapshot and agent state.
    """

    def __init__(
        self,
        agent_name: str,
        store: Any,
        agent: Any,
        *,
        capture_activity: bool = True,
        event_key_prefix: str | None = None,
    ):
        self._agent_name = agent_name
        self._store = store
        self._agent = agent
        self._capture_activity = capture_activity
        # Attached agents require a host-scoped namespace to avoid collisions.
        self._event_key_prefix = event_key_prefix or agent_name
        # Durable buffering preserves partial text across process interruption.
        # Sequence numbers restart for each assistant response.
        self._open_text = OpenTextSegment(store, self._event_key_prefix)
        self._recovered_open_text: bool = False
        self._chunk_seq: int = 0
        # Retain tasks for child runs that lack SubAgentManager persistence.
        self._subagent_tasks: dict[str, dict] = {}
        # Secondary outputs may skip ``start``, so totals restore lazily before use.
        self._token_totals_restored: bool = False
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cached_tokens: int = 0
        # Event writes queued on the store's affinity thread, drained at
        # turn boundaries. Populated before recovery so the constructor can
        # queue without an event loop.
        self._pending_writes: list[Future] = []
        # Recover immediately so read-only resumes expose interrupted text.
        self._recover_open_text()

    def _current_turn_branch(self) -> tuple[int | None, int | None]:
        """Return the active positive turn and branch identifiers, if available."""
        agent = self._agent
        if agent is None:
            return None, None
        ti = getattr(agent, "_turn_index", None)
        bi = getattr(agent, "_branch_id", None)
        if isinstance(ti, int) and ti > 0 and isinstance(bi, int) and bi > 0:
            return ti, bi
        return None, None

    def _current_parent_path(self) -> list[tuple[int, int]] | None:
        """Return a mutable snapshot of the active branch lineage, if available."""
        agent = self._agent
        if agent is None:
            return None
        path = getattr(agent, "_parent_branch_path", None)
        if not isinstance(path, list):
            return None
        return [tuple(p) for p in path]

    def _record(self, event_type: str, data: dict) -> None:
        """Record a non-text event, closing the open text segment first.

        A routed non-text event defines a segment boundary, so buffered text
        must be persisted first to preserve event order.
        """
        self._recover_open_text()
        self._flush_text_segment()
        self._append_event(event_type, data)

    def _append_event(self, event_type: str, data: dict) -> None:
        """Append one event under this sink's configured namespace."""
        ti, bi = self._current_turn_branch()
        self._append_event_at(event_type, data, ti, bi, self._current_parent_path())

    def _append_event_at(
        self,
        event_type: str,
        data: dict,
        turn_index: int | None,
        branch_id: int | None,
        parent_branch_path: list[tuple[int, int]] | None,
    ) -> None:
        """Queue one event with explicit turn/branch/path stamps.

        Writes go to the store's affinity thread in submit order and are
        awaited at the next :meth:`drain`; stores without an affinity
        executor fall back to an inline append.
        """
        try:
            submit = getattr(self._store, "submit", None)
            if not callable(submit):
                self._store.append_event(
                    self._event_key_prefix,
                    event_type,
                    data,
                    turn_index=turn_index,
                    branch_id=branch_id,
                    parent_branch_path=parent_branch_path,
                )
                return
            self._pending_writes.append(
                submit(
                    self._store.append_event,
                    self._event_key_prefix,
                    event_type,
                    data,
                    turn_index=turn_index,
                    branch_id=branch_id,
                    parent_branch_path=parent_branch_path,
                )
            )
        except Exception as e:
            logger.warning("Session record failed", error=str(e), exc_info=True)

    async def drain(self) -> None:
        """Wait for every queued write to reach the store.

        Turn boundaries await this before reading or snapshotting so the
        persisted view includes everything dispatched during the turn.
        Failures surface here with the same warning the inline path used.

        Cancellation-safe: the in-flight write is shielded so it completes
        on its own, and writes not yet started are put back at the head of
        the queue for the next drain — a cancelled turn must not silently
        drop events it already emitted.
        """
        pending, self._pending_writes = self._pending_writes, []
        for idx, fut in enumerate(pending):
            try:
                await asyncio.shield(asyncio.wrap_future(fut))
            except asyncio.CancelledError:
                self._pending_writes[:0] = [f for f in pending[idx:] if not f.done()]
                raise
            except Exception as e:
                logger.warning("Session record failed", error=str(e), exc_info=True)

    def _ensure_token_totals_restored(self) -> None:
        """Seed cumulative totals from the persisted slot, exactly once.

        Counters are namespace-scoped and restored before accumulation so a
        resumed run extends rather than replaces prior totals.
        """
        if self._token_totals_restored:
            return
        self._token_totals_restored = True
        try:
            usage = self._store.state.get(f"{self._event_key_prefix}:token_usage")
            if isinstance(usage, dict):
                self._total_input_tokens = usage.get("total_input_tokens", 0)
                self._total_output_tokens = usage.get("total_output_tokens", 0)
                self._total_cached_tokens = usage.get("total_cached_tokens", 0)
        except (KeyError, TypeError):
            pass

    async def start(self) -> None:
        await self.drain()
        self._ensure_token_totals_restored()
        # Finalize any segment left by an interrupted process.
        self._recover_open_text()

    async def stop(self) -> None:
        await self.drain()

    async def write(self, text: str) -> None:
        self._ingest_text(text)

    async def write_stream(self, chunk: str) -> None:
        self._ingest_text(chunk)

    def _ingest_text(self, chunk: str) -> None:
        """Buffer a streamed text chunk into the open segment.

        Chunks accumulate in durable state and flush as one ``text_chunk`` at
        the next non-text event or processing boundary.
        """
        self._recover_open_text()
        if chunk:
            self._open_text.append(chunk)

    def _recover_open_text(self) -> None:
        """Flush a segment orphaned by a crashed process, exactly once.

        Recovery is idempotent and runs during construction so view-only resumes
        surface partial text. The recovered event uses the interrupted turn's
        stamps rather than the resumed agent's current turn.
        """
        if self._recovered_open_text:
            return
        self._recovered_open_text = True
        recovered = self._open_text.recover()
        if not recovered:
            return
        ti, bi, ppath = last_persisted_turn_branch(self._store, self._event_key_prefix)
        self._append_event_at(
            "text_chunk",
            {
                "content": recovered,
                "chunk_seq": self._chunk_seq,
                "finalize": "recovered",
            },
            ti,
            bi,
            ppath,
        )
        self._chunk_seq += 1

    def _flush_text_segment(self, *, finalize: str | None = None) -> None:
        """Write the open segment as one ``text_chunk`` event and clear it."""
        text = self._open_text.take()
        if text:
            self._append_text_chunk(text, finalize=finalize)

    def _append_text_chunk(self, content: str, *, finalize: str | None = None) -> None:
        data: dict[str, Any] = {"content": content, "chunk_seq": self._chunk_seq}
        if finalize:
            data["finalize"] = finalize
        self._chunk_seq += 1
        self._append_event("text_chunk", data)

    async def flush(self) -> None:
        await self.drain()

    async def on_processing_start(self, *, request_id: str | None = None) -> None:
        # Sequence numbers are local to one assistant response.
        self._chunk_seq = 0
        payload = {"request_id": request_id} if request_id is not None else {}
        self._record("processing_start", payload)

    async def on_processing_end(self) -> None:
        # The snapshot below must observe every event queued during the
        # turn, so drain before reading the store.
        await self.drain()
        self._record("processing_end", {})
        # _record flushed the open text segment and queued processing_end
        # itself; drain again so the turn's final events are durable and
        # the snapshot watermark below cannot run ahead of the log.
        await self.drain()

        # Snapshots are derived caches; use live controller messages when event
        # replay cannot yet reconstruct the conversation. The full event log is
        # only replayed when the controller is absent; otherwise a single-key
        # read supplies ``last_event_id`` (the O(N) full scan here made every
        # turn cost linear in total session length).
        try:
            if self._agent and hasattr(self._agent, "controller"):
                messages = self._agent.controller.conversation.snapshot_messages()
            else:
                events = self._store.get_events(self._event_key_prefix)
                messages = replay_conversation(events, include_metadata=True)
            try:
                last_event_id = self._store.max_event_id(self._event_key_prefix)
            except AttributeError:
                # Duck-typed store without the counter: fall back to a scan.
                last_event_id = 0
                for evt in self._store.get_events(self._event_key_prefix):
                    eid = evt.get("event_id")
                    if isinstance(eid, int) and eid > last_event_id:
                        last_event_id = eid
            self._store.save_conversation(self._event_key_prefix, messages)
            try:
                self._store.state[f"{self._event_key_prefix}:snapshot_event_id"] = (
                    last_event_id
                )
                # The snapshot is the "last active branch" view; tag it with
                # the agent's branch so resume can reject it when the target
                # branch differs and rebuild via replay instead.
                agent = getattr(self, "_agent", None)
                if agent is not None:
                    branch = {
                        "turn_index": getattr(agent, "_turn_index", None),
                        "branch_id": getattr(agent, "_branch_id", None),
                        "parent_branch_path": getattr(
                            agent, "_parent_branch_path", None
                        ),
                    }
                    if (
                        isinstance(branch["turn_index"], int)
                        and branch["turn_index"] > 0
                        and isinstance(branch["branch_id"], int)
                        and branch["branch_id"] > 0
                    ):
                        self._store.state[
                            f"{self._event_key_prefix}:snapshot_branch"
                        ] = branch
                    else:
                        # The snapshot was rewritten above but the agent's
                        # branch state is missing/invalid; clear any stale tag
                        # from a prior run so resume does not trust a branch
                        # that no longer matches this snapshot.
                        self._store.state.pop(
                            f"{self._event_key_prefix}:snapshot_branch", None
                        )
            except Exception as e:
                logger.warning(
                    "Failed to save snapshot_event_id",
                    error=str(e),
                    exc_info=True,
                )
        except Exception as e:
            logger.warning("Conversation snapshot failed", error=str(e))

        # Token totals have a separate cumulative writer and must not be overwritten.
        try:
            if self._agent:
                state_kwargs = {}

                if hasattr(self._agent, "session") and self._agent.session:
                    pad = self._agent.session.scratchpad
                    if hasattr(pad, "to_dict"):
                        state_kwargs["scratchpad"] = pad.to_dict()

                # A per-call token shape here would clobber cumulative totals.

                if state_kwargs:
                    self._store.save_state(self._event_key_prefix, **state_kwargs)
        except Exception as e:
            logger.warning("State save failed", error=str(e), exc_info=True)

        # Keep the on-disk event log consistent with the snapshot for immediate
        # out-of-process readers; at most one buffered batch is flushed per turn.
        try:
            flush = getattr(self._store, "flush", None)
            if callable(flush):
                flush()
        except Exception as e:
            logger.warning(
                "Events flush at turn end failed", error=str(e), exc_info=True
            )

    def on_activity(self, activity_type: str, detail: str) -> None:
        if not self._capture_activity:
            return
        name, info = _parse_detail(detail)
        self._record_activity(activity_type, name, info, {})

    def on_assistant_image(
        self,
        url: str,
        *,
        detail: str = "auto",
        source_type: str | None = None,
        source_name: str | None = None,
        revised_prompt: str | None = None,
    ) -> None:
        """Append an ``assistant_image`` event to the session log.

        Image bytes already reside in artifact storage; this event records the
        metadata required by resume and history consumers.
        """
        payload: dict = {
            "url": url,
            "detail": detail,
        }
        if source_type is not None:
            payload["source_type"] = source_type
        if source_name is not None:
            payload["source_name"] = source_name
        if revised_prompt is not None:
            payload["revised_prompt"] = revised_prompt
        self._record("assistant_image", payload)

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict
    ) -> None:
        if not self._capture_activity:
            return
        name, info = _parse_detail(detail)
        self._record_activity(activity_type, name, info, metadata)

    async def emit(self, event: OutputEvent) -> None:
        """Native event consumer.

        Translate each native event to the same persistence path used by the
        corresponding output hooks.
        """
        match event.type:
            case "text":
                content = event.content
                if isinstance(content, str) and content:
                    self._ingest_text(content)
            case "processing_start":
                await self.on_processing_start(
                    request_id=event.payload.get("request_id")
                )
            case "processing_end":
                await self.on_processing_end()
            case "user_input":
                # The agent writes the canonical user-input event directly.
                pass
            case "assistant_image":
                payload = event.payload
                self.on_assistant_image(
                    payload["url"],
                    detail=payload.get("detail", "auto"),
                    source_type=payload.get("source_type"),
                    source_name=payload.get("source_name"),
                    revised_prompt=payload.get("revised_prompt"),
                )
            case "resume_batch":
                # Replay batches are consumed by readers, not persisted again.
                pass
            case "ask_text" | "confirm" | "selection" | "card":
                if not self._capture_activity:
                    return
                self._record(
                    event.type,
                    {
                        "ui_event_id": event.id,
                        "interactive": bool(event.interactive),
                        "surface": event.surface,
                        "payload": dict(event.payload),
                    },
                )
            case "ui_supersede":
                if not self._capture_activity:
                    return
                self._record(
                    "ui_supersede",
                    {"ui_event_id": event.payload.get("event_id")},
                )
            case _:
                if not self._capture_activity:
                    return
                detail = event.content if isinstance(event.content, str) else ""
                name, info = _parse_detail(detail)
                self._record_activity(event.type, name, info, event.payload or {})

    def on_supersede(self, event_id: str) -> None:
        """Persist the terminal lifecycle marker used by history reconstruction."""
        if self._capture_activity:
            self._record("ui_supersede", {"ui_event_id": event_id})
