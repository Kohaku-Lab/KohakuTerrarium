"""Persist routed agent output and activity events to a session store."""

import json
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.text_buffer import (
    OpenTextSegment,
    last_persisted_turn_branch,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SessionOutput(OutputModule):
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
        """Append one event with explicit turn/branch/path stamps."""
        try:
            self._store.append_event(
                self._event_key_prefix,
                event_type,
                data,
                turn_index=turn_index,
                branch_id=branch_id,
                parent_branch_path=parent_branch_path,
            )
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
        self._ensure_token_totals_restored()
        # Finalize any segment left by an interrupted process.
        self._recover_open_text()

    async def stop(self) -> None:
        pass

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
        pass

    async def on_processing_start(self, *, request_id: str | None = None) -> None:
        # Sequence numbers are local to one assistant response.
        self._chunk_seq = 0
        payload = {"request_id": request_id} if request_id is not None else {}
        self._record("processing_start", payload)

    async def on_processing_end(self) -> None:
        self._record("processing_end", {})

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
            last_event_id = self._store.max_event_id(self._event_key_prefix)
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
            case _:
                if not self._capture_activity:
                    return
                detail = event.content if isinstance(event.content, str) else ""
                name, info = _parse_detail(detail)
                self._record_activity(event.type, name, info, event.payload or {})

    # String targets keep activity dispatch declarative at class scope.
    _ACTIVITY_HANDLERS: dict[str, str] = {
        "trigger_fired": "_handle_trigger_fired",
        "tool_start": "_handle_tool_start",
        "tool_done": "_handle_tool_done",
        "tool_error": "_handle_tool_error",
        "subagent_start": "_handle_subagent_start",
        "subagent_done": "_handle_subagent_done",
        "subagent_error": "_handle_subagent_error",
        "subagent_token_update": "_handle_subagent_token_update",
        "token_usage": "_handle_token_usage",
        "compact_start": "_handle_compact_start",
        "compact_complete": "_handle_compact_complete",
        "compact_skipped": "_handle_compact_skipped",
        "background_result": "_handle_background_result",
        "processing_complete": "_handle_processing_complete",
        "processing_error": "_handle_processing_error",
        "context_cleared": "_handle_context_cleared",
        "tool_wait": "_handle_tool_wait",
        "compact_decision": "_handle_compact_decision",
        "turn_token_usage": "_handle_turn_token_usage",
        "plugin_hook_timing": "_handle_plugin_hook_timing",
        "cache_stats": "_handle_cache_stats",
        "scratchpad_write": "_handle_scratchpad_write",
        # Suppress duplicate activity rows for input already persisted by the agent.
        "user_input_injected": "_handle_user_input_injected",
    }

    def _record_activity(
        self, activity_type: str, name: str, detail: str, metadata: dict
    ) -> None:
        handler_name = self._ACTIVITY_HANDLERS.get(activity_type)
        if handler_name:
            getattr(self, handler_name)(name, detail, metadata)
        elif activity_type.startswith("subagent_tool_"):
            self._handle_subagent_tool(activity_type, name, detail, metadata)
        else:
            self._record(
                f"activity:{activity_type}",
                {"name": name, "detail": detail, **metadata},
            )

    def _handle_trigger_fired(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "trigger_fired",
            {
                "trigger_id": metadata.get("trigger_id", ""),
                "channel": metadata.get("channel", ""),
                "sender": metadata.get("sender", ""),
                "content": metadata.get("content", ""),
            },
        )

    def _handle_tool_start(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "tool_call",
            {
                "name": name,
                "call_id": metadata.get("job_id", ""),
                "args": metadata.get("args", {}),
            },
        )

    def _handle_tool_done(self, name: str, detail: str, metadata: dict) -> None:
        event_data: dict[str, Any] = {
            "name": name,
            "call_id": metadata.get("job_id", ""),
            "output": metadata.get("result", metadata.get("output", detail)),
            "exit_code": 0,
        }
        # Persist preview metadata so history reload need not read the file again.
        canvas_preview = metadata.get("canvas_preview")
        if canvas_preview:
            event_data["canvas_preview"] = canvas_preview
        tool_metadata = metadata.get("tool_metadata")
        if isinstance(tool_metadata, dict):
            event_data["tool_metadata"] = dict(tool_metadata)
        self._record("tool_result", event_data)

    def _handle_tool_error(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "tool_result",
            {
                "name": name,
                "call_id": metadata.get("job_id", ""),
                "output": metadata.get("result", detail),
                "exit_code": 1,
                "error": metadata.get("error", detail),
                "interrupted": bool(metadata.get("interrupted", False)),
                "cancelled": bool(metadata.get("cancelled", False)),
                "final_state": metadata.get("final_state", "error"),
            },
        )

    def _handle_subagent_start(self, name: str, detail: str, metadata: dict) -> None:
        task = metadata.get("task", detail)
        job_id = metadata.get("job_id", "")
        if job_id:
            # Completion may need the task to synthesize missing child history.
            self._subagent_tasks[job_id] = {
                "name": name,
                "task": task,
            }
        self._record(
            "subagent_call",
            {
                "name": name,
                "task": task,
                "job_id": job_id,
                "background": bool(metadata.get("background", False)),
            },
        )

    def _handle_subagent_done(self, name: str, detail: str, metadata: dict) -> None:
        job_id = metadata.get("job_id", "")
        name = _subagent_name(name, metadata)
        output_text = metadata.get("result", detail)
        self._record(
            "subagent_result",
            {
                "name": name,
                "job_id": job_id,
                "output": output_text,
                "tools_used": metadata.get("tools_used", []),
                "turns": metadata.get("turns", 0),
                "duration": metadata.get("duration", 0),
                **_token_metadata(metadata),
            },
        )
        # Preserve history for child runs outside SubAgentManager.
        self._persist_subagent_conversation(
            name, job_id, output_text, success=True, metadata=metadata
        )

    def _handle_subagent_token_update(
        self, name: str, detail: str, metadata: dict
    ) -> None:
        self._record(
            "subagent_token_usage",
            {
                "name": _subagent_name(name, metadata),
                "job_id": metadata.get("job_id", ""),
                **_token_metadata(metadata),
            },
        )

    def _handle_subagent_error(self, name: str, detail: str, metadata: dict) -> None:
        job_id = metadata.get("job_id", "")
        name = _subagent_name(name, metadata)
        output_text = metadata.get("result", detail)
        self._record(
            "subagent_result",
            {
                "name": name,
                "job_id": job_id,
                "output": output_text,
                "error": metadata.get("error", detail),
                "success": False,
                "interrupted": bool(metadata.get("interrupted", False)),
                "cancelled": bool(metadata.get("cancelled", False)),
                "final_state": metadata.get("final_state", "error"),
                "tools_used": metadata.get("tools_used", []),
                "turns": metadata.get("turns", 0),
                "duration": metadata.get("duration", 0),
                **_token_metadata(metadata),
            },
        )
        self._persist_subagent_conversation(
            name, job_id, output_text, success=False, metadata=metadata
        )

    def _persist_subagent_conversation(
        self,
        name: str,
        job_id: str,
        output_text: str,
        *,
        success: bool,
        metadata: dict,
    ) -> None:
        """Persist a minimal conversation when no full child history exists.

        Managed sub-agents already store their complete conversation; this path
        preserves tool-like child runs that bypass that persistence.
        """
        task_record = self._subagent_tasks.pop(job_id, None)
        task_text = task_record.get("task", "") if task_record is not None else ""
        try:
            # Never replace a full managed conversation with a synthetic stub.
            run = self._store.next_subagent_run(self._agent_name, name)
            convo = [
                {"role": "user", "content": task_text},
                {"role": "assistant", "content": output_text or ""},
            ]
            self._store.save_subagent(
                parent=self._agent_name,
                name=name,
                run=run,
                meta={
                    "task": task_text,
                    "turns": metadata.get("turns", 0),
                    "tools_used": metadata.get("tools_used", []),
                    "success": success,
                    "duration": metadata.get("duration", 0),
                    "output_preview": (output_text or "")[:500],
                    "source": "session_output",
                },
                conv_json=json.dumps(convo),
            )
        except Exception as e:
            logger.warning(
                "Failed to persist sub-agent conversation via SessionOutput",
                error=str(e),
                exc_info=True,
            )

    def _handle_token_usage(self, name: str, detail: str, metadata: dict) -> None:
        self._ensure_token_totals_restored()
        prompt = metadata.get("prompt_tokens", 0)
        completion = metadata.get("completion_tokens", 0)
        cached = metadata.get("cached_tokens", 0)
        self._total_input_tokens += prompt
        self._total_output_tokens += completion
        self._total_cached_tokens += cached
        self._record(
            "token_usage",
            {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": metadata.get("total_tokens", 0),
                "cached_tokens": cached,
            },
        )
        # Namespace cumulative totals so attached agents cannot collide with hosts.
        try:
            self._store.save_state(
                self._event_key_prefix,
                token_usage={
                    "total_input_tokens": self._total_input_tokens,
                    "total_output_tokens": self._total_output_tokens,
                    "total_cached_tokens": self._total_cached_tokens,
                    "last_prompt_tokens": prompt,
                },
            )
        except Exception as e:
            logger.warning(
                "Failed to save token usage state", error=str(e), exc_info=True
            )

    def _handle_compact_start(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "compact_start",
            {"round": metadata.get("round", 0)},
        )

    def _handle_compact_complete(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "compact_complete",
            {
                "round": metadata.get("round", 0),
                "summary": metadata.get("summary", ""),
                "messages_compacted": metadata.get("messages_compacted", 0),
                "replaced_from_event_id": metadata.get("replaced_from_event_id"),
                "replaced_to_event_id": metadata.get("replaced_to_event_id"),
                "compact_path": metadata.get("compact_path"),
                "turn_index": metadata.get("turn_index"),
                "branch_id": metadata.get("branch_id"),
                "parent_branch_path": metadata.get("parent_branch_path"),
            },
        )

    def _handle_compact_skipped(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "compact_skipped",
            {
                "round": metadata.get("round", 0),
                "reason": metadata.get("reason", ""),
            },
        )

    def _handle_background_result(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "background_result",
            {
                "job_id": metadata.get("job_id", ""),
                "kind": metadata.get("kind", "tool"),
                "label": metadata.get("label", ""),
            },
        )

    def _handle_subagent_tool(
        self, activity_type: str, name: str, detail: str, metadata: dict
    ) -> None:
        self._record(
            "subagent_tool",
            {
                "subagent": metadata.get("subagent", name),
                "tool_name": metadata.get("tool", ""),
                "activity": activity_type.replace("subagent_", ""),
                "detail": metadata.get("detail", detail),
                "job_id": metadata.get("job_id", ""),
            },
        )

    def _handle_context_cleared(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "context_cleared",
            {"messages_cleared": metadata.get("messages_cleared", 0)},
        )

    def _handle_processing_error(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "processing_error",
            {
                "error_type": metadata.get("error_type", "Error"),
                "error": metadata.get("error", detail),
            },
        )

    def _handle_processing_complete(
        self, name: str, detail: str, metadata: dict
    ) -> None:
        self._record(
            "processing_complete",
            {
                "trigger_channel": metadata.get("trigger_channel", ""),
                "trigger_sender": metadata.get("trigger_sender", ""),
                "output_preview": metadata.get("output_preview", ""),
            },
        )

    def _handle_tool_wait(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "tool_wait",
            {
                "tool": metadata.get("tool", name),
                "wait_ms": metadata.get("wait_ms", 0),
                "reason": metadata.get("reason", "serial_lock"),
            },
        )

    def _handle_compact_decision(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "compact_decision",
            {
                "reason": metadata.get("reason", "unknown"),
                "tokens_before": metadata.get("tokens_before", 0),
                "tokens_after": metadata.get("tokens_after", 0),
                "skipped": bool(metadata.get("skipped", False)),
            },
        )

    def _handle_turn_token_usage(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "turn_token_usage",
            {
                "turn_index": metadata.get("turn_index", 0),
                "prompt_tokens": metadata.get("prompt_tokens", 0),
                "completion_tokens": metadata.get("completion_tokens", 0),
                "cached_tokens": metadata.get("cached_tokens", 0),
                "total_tokens": metadata.get("total_tokens", 0),
            },
        )
        # Persist the derived rollup at the source to avoid repeated event scans.
        turn_index = metadata.get("turn_index", 0)
        if self._store and isinstance(turn_index, int) and turn_index > 0:
            try:
                self._store.save_turn_rollup(
                    self._event_key_prefix,
                    turn_index,
                    {
                        "started_at": metadata.get("started_at"),
                        "ended_at": metadata.get("ended_at"),
                        "tokens_in": int(metadata.get("prompt_tokens") or 0),
                        "tokens_out": int(metadata.get("completion_tokens") or 0),
                        "tokens_cached": int(metadata.get("cached_tokens") or 0),
                        "cost_usd": metadata.get("cost_usd"),
                    },
                )
            except Exception as e:
                logger.warning(
                    "save_turn_rollup failed",
                    error=str(e),
                    turn_index=turn_index,
                    exc_info=True,
                )

    def _handle_plugin_hook_timing(
        self, name: str, detail: str, metadata: dict
    ) -> None:
        self._record(
            "plugin_hook_timing",
            {
                "hook": metadata.get("hook", name),
                "plugin": metadata.get("plugin", ""),
                "duration_ms": metadata.get("duration_ms", 0),
                "blocked": bool(metadata.get("blocked", False)),
            },
        )

    def _handle_cache_stats(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "cache_stats",
            {
                "agent": metadata.get("agent", self._agent_name),
                "cache_write": metadata.get("cache_write", 0),
                "cache_read": metadata.get("cache_read", 0),
                "cache_hit_ratio": metadata.get("cache_hit_ratio", 0.0),
            },
        )

    def _handle_scratchpad_write(self, name: str, detail: str, metadata: dict) -> None:
        self._record(
            "scratchpad_write",
            {
                "agent": metadata.get("agent", self._agent_name),
                "key": metadata.get("key", name),
                "action": metadata.get("action", "set"),
                "size_bytes": metadata.get("size_bytes", 0),
            },
        )

    def _handle_user_input_injected(
        self, name: str, detail: str, metadata: dict
    ) -> None:
        # The agent already wrote the canonical event with branch attribution.
        return


def _subagent_name(fallback: str, metadata: dict) -> str:
    raw = metadata.get("subagent") or metadata.get("subagent_name")
    if isinstance(raw, str) and raw:
        return raw
    job_id = str(metadata.get("job_id") or "")
    if fallback == "agent" and job_id.startswith("agent_"):
        body = job_id[len("agent_") :]
        if "_" in body:
            return body.rsplit("_", 1)[0]
    return fallback


def _token_metadata(metadata: dict) -> dict[str, Any]:
    prompt = metadata.get("prompt_tokens")
    completion = metadata.get("completion_tokens")
    total = metadata.get("total_tokens")
    cached = metadata.get("cached_tokens")
    if prompt is None:
        prompt = metadata.get("tokens_in", 0)
    if completion is None:
        completion = metadata.get("tokens_out", 0)
    if cached is None:
        cached = metadata.get("tokens_cached", 0)
    if total is None:
        try:
            total = int(prompt or 0) + int(completion or 0)
        except (TypeError, ValueError):
            total = 0
    data = {
        "total_tokens": total or 0,
        "prompt_tokens": prompt or 0,
        "completion_tokens": completion or 0,
        "cached_tokens": cached or 0,
    }
    if "cost_usd" in metadata:
        data["cost_usd"] = metadata.get("cost_usd")
    return data


def _parse_detail(detail: str) -> tuple[str, str]:
    """Extract [name] prefix from detail string.

    Handles nested brackets by finding ``] `` (closing bracket + space).
    """
    try:
        if detail.startswith("["):
            # The delimiter preserves nested brackets inside the label.
            end = detail.index("] ", 1)
            return detail[1:end], detail[end + 2 :]
    except ValueError:
        # A bare bracketed label has no detail suffix.
        try:
            if detail.startswith("[") and detail.endswith("]"):
                return detail[1:-1], ""
        except ValueError:
            pass
    return "unknown", detail
