"""
SessionOutput - OutputModule that persists events to SessionStore.

Added as a secondary output on the agent's output router (same pattern
as the WS StreamOutput). Captures text, tool activity, processing state,
trigger events, and token usage without modifying the processing loop.
"""

import json
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SessionOutput(OutputModule):
    """Output module that records events to a SessionStore.

    Accumulates streaming text chunks and flushes as one event
    on processing_end. Tool/subagent activity can be recorded immediately
    when enabled. Saves conversation snapshot and agent state after each
    processing cycle.
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
        self._agent = agent  # direct reference, not dict lookup
        self._capture_activity = capture_activity
        # Wave F: attached agents write events under a custom key prefix
        # (``<host>:attached:<role>:<attach_seq>``). Defaults to the
        # agent's own name for the standard one-agent-per-store case.
        self._event_key_prefix = event_key_prefix or agent_name
        # Wave C: streaming chunks land as ``text_chunk`` events on the
        # append bus directly; the old ``_text_buffer`` flush-on-turn-end
        # path is gone. ``_chunk_seq`` counts within one assistant
        # response and resets at each ``processing_start``.
        self._chunk_seq: int = 0
        # Wave C: track ``subagent_start`` tasks so a later
        # ``subagent_done`` can persist a minimal conversation record
        # for child agents that ran outside SubAgentManager.
        self._subagent_tasks: dict[str, dict] = {}
        # Cumulative API token usage across the session
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cached_tokens: int = 0

    def _current_turn_branch(self) -> tuple[int | None, int | None]:
        """Return ``(turn_index, branch_id)`` from the agent, or
        ``(None, None)`` for sinks without an attached agent.
        """
        agent = self._agent
        if agent is None:
            return None, None
        ti = getattr(agent, "_turn_index", None)
        bi = getattr(agent, "_branch_id", None)
        if isinstance(ti, int) and ti > 0 and isinstance(bi, int) and bi > 0:
            return ti, bi
        return None, None

    def _current_parent_path(self) -> list[tuple[int, int]] | None:
        """Snapshot of the agent's branch lineage at this moment.

        Returned as a fresh list (caller may mutate). ``None`` when
        the agent or its path has not been initialised.
        """
        agent = self._agent
        if agent is None:
            return None
        path = getattr(agent, "_parent_branch_path", None)
        if not isinstance(path, list):
            return None
        return [tuple(p) for p in path]

    def _record(self, event_type: str, data: dict) -> None:
        """Record an event under this sink's event-key prefix.

        Wave F: ``event_key_prefix`` replaces the agent-name namespace
        when the agent is attached to a host session. Defaults to the
        agent's own name (the pre-Wave-F behavior).
        """
        ti, bi = self._current_turn_branch()
        try:
            self._store.append_event(
                self._event_key_prefix,
                event_type,
                data,
                turn_index=ti,
                branch_id=bi,
                parent_branch_path=self._current_parent_path(),
            )
        except Exception as e:
            logger.debug("Session record failed", error=str(e))

    async def start(self) -> None:
        # Restore cumulative token totals from session state. Wave F:
        # attached agents have their own ``<host>:attached:<role>:<seq>``
        # prefix so restart restores each sink's own counters.
        try:
            usage = self._store.state.get(f"{self._event_key_prefix}:token_usage")
            if isinstance(usage, dict):
                self._total_input_tokens = usage.get("total_input_tokens", 0)
                self._total_output_tokens = usage.get("total_output_tokens", 0)
                self._total_cached_tokens = usage.get("total_cached_tokens", 0)
        except (KeyError, TypeError):
            pass

    async def stop(self) -> None:
        pass

    async def write(self, text: str) -> None:
        # Wave C: non-streaming writes still go to the append bus as a
        # ``text_chunk`` event. Replay collapses consecutive chunks
        # into one logical assistant message.
        if text:
            self._emit_text_chunk(text)

    async def write_stream(self, chunk: str) -> None:
        if chunk:
            self._emit_text_chunk(chunk)

    def _emit_text_chunk(self, chunk: str) -> None:
        """Append a Wave C ``text_chunk`` event.

        Wave F: honours ``event_key_prefix`` so attached agents write
        streaming chunks under their attached namespace.
        """
        seq = self._chunk_seq
        self._chunk_seq += 1
        ti, bi = self._current_turn_branch()
        try:
            self._store.append_event(
                self._event_key_prefix,
                "text_chunk",
                {"content": chunk, "chunk_seq": seq},
                turn_index=ti,
                branch_id=bi,
                parent_branch_path=self._current_parent_path(),
            )
        except Exception as e:
            logger.debug("text_chunk record failed", error=str(e))

    async def flush(self) -> None:
        pass

    async def on_processing_start(self) -> None:
        # Wave C: chunk_seq is per-assistant-response.
        self._chunk_seq = 0
        self._record("processing_start", {})

    async def on_processing_end(self) -> None:
        self._record("processing_end", {})

        # Wave C: snapshot is now a derived cache rebuilt from the
        # event stream. Falls back to the in-memory controller messages
        # if replay is empty (e.g. very first turn before any events
        # landed). Wave F: all reads/writes are keyed by
        # ``_event_key_prefix`` so attached agents' snapshots/state
        # live under their attached namespace.
        try:
            events = self._store.get_events(self._event_key_prefix)
            if self._agent and hasattr(self._agent, "controller"):
                messages = self._agent.controller.conversation.to_messages()
            else:
                messages = replay_conversation(events)
            last_event_id = 0
            for evt in events:
                eid = evt.get("event_id")
                if isinstance(eid, int) and eid > last_event_id:
                    last_event_id = eid
            self._store.save_conversation(self._event_key_prefix, messages)
            try:
                self._store.state[f"{self._event_key_prefix}:snapshot_event_id"] = (
                    last_event_id
                )
            except Exception as e:
                logger.debug(
                    "Failed to save snapshot_event_id",
                    error=str(e),
                    exc_info=True,
                )
        except Exception as e:
            logger.warning("Conversation snapshot failed", error=str(e))

        # Save agent state (scratchpad, turn count, token usage)
        try:
            if self._agent:
                state_kwargs = {}

                # Scratchpad
                if hasattr(self._agent, "session") and self._agent.session:
                    pad = self._agent.session.scratchpad
                    if hasattr(pad, "to_dict"):
                        state_kwargs["scratchpad"] = pad.to_dict()

                # Token usage from controller
                if hasattr(self._agent, "controller"):
                    usage = getattr(self._agent.controller, "_last_usage", {})
                    if usage:
                        state_kwargs["token_usage"] = usage

                if state_kwargs:
                    self._store.save_state(self._event_key_prefix, **state_kwargs)
        except Exception as e:
            logger.debug("State save failed", error=str(e))

        # Flush the events cache so the per-turn snapshot we just wrote
        # is consistent with the on-disk event log. Without this, the
        # ``events`` table can hold up to ``flush_interval`` seconds of
        # writes in memory; a UI that switches to a different session
        # tab and immediately re-opens the file (or any out-of-process
        # reader) sees a truncated history. The flush is bounded —
        # there is at most one batch per turn, so the cost is small.
        try:
            flush = getattr(self._store, "flush", None)
            if callable(flush):
                flush()
        except Exception as e:
            logger.debug("Events flush at turn end failed", error=str(e))

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

        The image bytes are already on disk (written by the controller
        via ``SessionStore.write_artifact``). This just records the
        metadata so resume + event-log consumers can surface it.
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

        Translates each ``OutputEvent`` to the same persistence calls
        the legacy hooks would make. The mapping is byte-identical: a
        renderer override here records the same session-log records
        SessionOutput records when called via ``write_stream`` /
        ``on_activity_with_metadata`` / ``on_processing_*`` /
        ``on_assistant_image``.
        """
        match event.type:
            case "text":
                content = event.content
                if isinstance(content, str) and content:
                    self._emit_text_chunk(content)
            case "processing_start":
                await self.on_processing_start()
            case "processing_end":
                await self.on_processing_end()
            case "user_input":
                # SessionOutput historically does not record user_input
                # via OutputModule (the agent records it elsewhere).
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
                # SessionOutput is a writer, not a replayer.
                pass
            case _:
                if not self._capture_activity:
                    return
                detail = event.content if isinstance(event.content, str) else ""
                name, info = _parse_detail(detail)
                self._record_activity(event.type, name, info, event.payload or {})

    # Dispatch table: activity_type -> handler method name
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
        "processing_complete": "_handle_processing_complete",
        "processing_error": "_handle_processing_error",
        "context_cleared": "_handle_context_cleared",
        # Wave B additive event types — emit via notify_activity.
        "tool_wait": "_handle_tool_wait",
        "compact_decision": "_handle_compact_decision",
        "turn_token_usage": "_handle_turn_token_usage",
        "plugin_hook_timing": "_handle_plugin_hook_timing",
        "cache_stats": "_handle_cache_stats",
        "scratchpad_write": "_handle_scratchpad_write",
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
        self._record(
            "tool_result",
            {
                "name": name,
                "call_id": metadata.get("job_id", ""),
                "output": metadata.get("result", metadata.get("output", detail)),
                "exit_code": 0,
            },
        )

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
            # Wave C: remember the task so ``subagent_done`` can persist
            # a minimal child conversation for plugin-spawned agents.
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
        # Wave C: persist a minimal child conversation so plugin-spawned
        # agents (no SubAgentManager) don't drop their history.
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
        """Wave C: save a minimal child conversation for SubAgentManager runs.

        The SubAgentManager path already writes the full conversation
        via ``SubAgent._build_result``; we only fill the gap when a
        child agent ran outside the manager (pre-Wave-F plugin flow).
        Wave F replacement: plugin-spawned ``Agent`` instances now
        attach to the host :class:`Session` via
        :func:`kohakuterrarium.session.attachment_service.attach_agent_to_session`
        and write their own events under
        ``<host>:attached:<role>:<attach_seq>:e<seq>``. This method
        stays for the SubAgent (tool-like) path.
        """
        task_record = self._subagent_tasks.pop(job_id, None)
        task_text = task_record.get("task", "") if task_record is not None else ""
        try:
            # Already persisted by SubAgentManager? Skip to avoid
            # overwriting the full conversation with a synthetic stub.
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
            logger.debug(
                "Failed to persist sub-agent conversation via SessionOutput",
                error=str(e),
                exc_info=True,
            )

    def _handle_token_usage(self, name: str, detail: str, metadata: dict) -> None:
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
        # Save cumulative totals to session state for fast resume.
        # Wave F: keyed under the attached-namespace prefix so attached
        # agents' counters don't collide with the host's.
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
            logger.debug(
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

    # ── Wave B additive event handlers ──

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
        # Populate the ``turn_rollup`` KVault table so the Trace / Cost
        # / Overview tabs in the viewer have data to render. The
        # endpoints fall back to events-derivation when this row is
        # missing, but writing the row at the source is cheaper for
        # subsequent reads.
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
                logger.debug(
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
            # Find "] " to handle labels with nested brackets like [name[id]]
            end = detail.index("] ", 1)
            return detail[1:end], detail[end + 2 :]
    except ValueError:
        # Fall back: no trailing content (bare [name])
        try:
            if detail.startswith("[") and detail.endswith("]"):
                return detail[1:-1], ""
        except ValueError:
            pass
    return "unknown", detail
