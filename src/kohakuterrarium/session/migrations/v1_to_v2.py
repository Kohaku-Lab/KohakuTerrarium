"""Migrate v1 sessions to an event-sourced v2 representation.

The ordered v1 event log is preferred over its post-compaction snapshot. Snapshot
messages are used only when no events exist. Compaction becomes explicit replace
ranges, and migrated events receive turn and branch attribution.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuterrarium.session.artifacts import artifacts_dir_for
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _iter_agents(source: SessionStore, meta: dict[str, Any]) -> list[str]:
    """Return every agent name worth replaying into the v2 store."""
    names = list(meta.get("agents") or [])
    for name in source.discover_agents_from_events():
        if name and name not in names:
            names.append(name)
    return names


def _coerce_args(args: Any) -> str:
    """Best-effort serialisation for ``tool_call.arguments`` (always JSON str)."""
    if isinstance(args, str):
        return args
    if args is None:
        return "{}"
    try:
        return json.dumps(args)
    except (TypeError, ValueError):
        return "{}"


def _flush_pending_tool_calls(
    pending: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    """Drain pending v1 tool_call events into a v2 ``assistant_tool_calls``."""
    if not pending:
        return None
    tool_calls: list[dict[str, Any]] = []
    for tc in pending:
        tool_calls.append(
            {
                "id": tc.get("call_id") or tc.get("id") or "",
                "type": "function",
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": _coerce_args(tc.get("args", "{}")),
                },
            }
        )
    pending.clear()
    return ("assistant_tool_calls", {"tool_calls": tool_calls, "content": ""})


def _translate_v1_events(
    v1_events: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], int]]:
    """Translate ordered v1 events into v2 event triples.

    User input opens turns, text receives monotonic chunk sequences, pending tool
    calls become assistant announcements, and compaction becomes replacement
    ranges. Observability events remain available for viewers.
    """
    out: list[tuple[str, dict[str, Any], int]] = []
    turn_index = 0
    chunk_seq = 0
    pending_tool_calls: list[dict[str, Any]] = []

    def _flush_text() -> None:
        # Text chunks are emitted immediately; this hook preserves dispatch symmetry.
        return None

    for evt in v1_events:
        etype = evt.get("type", "")
        if not etype:
            continue
        if etype == "user_input":
            tc = _flush_pending_tool_calls(pending_tool_calls)
            if tc:
                out.append((tc[0], tc[1], turn_index))
            turn_index += 1
            chunk_seq = 0
            content = evt.get("content", "")
            out.append(("user_input", {"content": content}, turn_index))
            out.append(("user_message", {"content": content}, turn_index))
        elif etype in ("text", "text_chunk"):
            tc = _flush_pending_tool_calls(pending_tool_calls)
            if tc:
                out.append((tc[0], tc[1], turn_index))
            content = evt.get("content", "")
            seq = evt.get("chunk_seq", chunk_seq)
            out.append(
                ("text_chunk", {"content": content, "chunk_seq": seq}, turn_index)
            )
            chunk_seq = (seq + 1) if isinstance(seq, int) else chunk_seq + 1
        elif etype == "tool_call":
            pending_tool_calls.append(dict(evt))
        elif etype == "tool_result":
            tc = _flush_pending_tool_calls(pending_tool_calls)
            if tc:
                out.append((tc[0], tc[1], turn_index))
            out.append(
                (
                    "tool_result",
                    {
                        "name": evt.get("name", ""),
                        "call_id": evt.get("call_id") or evt.get("job_id", ""),
                        "output": evt.get("output", ""),
                        "error": evt.get("error", ""),
                    },
                    turn_index,
                )
            )
        elif etype == "subagent_call":
            # Sub-agent events remain for viewers even though replay ignores them.
            out.append((etype, dict(evt), turn_index))
        elif etype == "subagent_result":
            out.append((etype, dict(evt), turn_index))
        elif etype == "compact_start":
            out.append((etype, dict(evt), turn_index))
        elif etype == "compact_complete":
            tc = _flush_pending_tool_calls(pending_tool_calls)
            if tc:
                out.append((tc[0], tc[1], turn_index))
            summary = evt.get("summary") or evt.get("content") or ""
            replaced_to = evt.get("replaced_to_event_id") or evt.get("event_id") or 0
            replaced_from = evt.get("replaced_from_event_id") or 1
            out.append(
                (
                    "compact_replace",
                    {
                        "summary_text": summary,
                        "replaced_from_event_id": replaced_from,
                        "replaced_to_event_id": replaced_to,
                        "messages_compacted": evt.get("messages_compacted", 0),
                        "round": evt.get("round", 0),
                    },
                    turn_index,
                )
            )
            # Retain the observability event alongside the state-bearing replacement.
            out.append(
                (
                    "compact_complete",
                    {
                        "summary": summary,
                        "round": evt.get("round", 0),
                        "messages_compacted": evt.get("messages_compacted", 0),
                    },
                    turn_index,
                )
            )
        elif etype in ("processing_start", "processing_end", "processing_error"):
            out.append((etype, dict(evt), turn_index))
        elif etype == "trigger_fired":
            out.append((etype, dict(evt), turn_index))
        elif etype == "context_cleared":
            out.append((etype, dict(evt), turn_index))
        elif etype == "token_usage":
            out.append((etype, dict(evt), turn_index))
        else:
            # Preserve unknown observability events; conversation replay ignores them.
            out.append((etype, dict(evt), turn_index))

    tc = _flush_pending_tool_calls(pending_tool_calls)
    if tc:
        out.append((tc[0], tc[1], turn_index))

    return out


def _synth_events_from_message(msg: dict[str, Any]) -> list[tuple[str, dict]]:
    """Produce v2 state-bearing events that replay to ``msg``."""
    role = (msg.get("role") or "").lower()
    content = msg.get("content", "")
    tool_calls = msg.get("tool_calls")
    name = msg.get("name", "")
    tool_call_id = msg.get("tool_call_id", "") or msg.get("call_id", "")

    if role == "system":
        return [("system_prompt_set", {"content": content})]
    if role == "user":
        return [("user_message", {"content": content})]
    if role == "assistant":
        events: list[tuple[str, dict]] = []
        text = content if isinstance(content, str) else ""
        if text:
            events.append(("text_chunk", {"content": text, "chunk_seq": 0}))
        if tool_calls:
            events.append(
                (
                    "assistant_tool_calls",
                    {"tool_calls": tool_calls, "content": text},
                )
            )
        if not events:
            events.append(("text_chunk", {"content": "", "chunk_seq": 0}))
        return events
    if role == "tool":
        return [
            (
                "tool_result",
                {
                    "name": name,
                    "call_id": tool_call_id,
                    "output": content,
                },
            )
        ]
    return []


def _synth_events_from_snapshot(
    messages: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], int]]:
    """Derive v2 event triples from a snapshot when the v1 log is empty."""
    out: list[tuple[str, dict[str, Any], int]] = []
    turn_index = 0
    for msg in messages:
        role = (msg.get("role") or "").lower()
        if role == "user":
            turn_index += 1
        synth = _synth_events_from_message(msg)
        for event_type, data in synth:
            out.append((event_type, data, max(turn_index, 1)))
    return out


def _write_migrated_events(
    dest: SessionStore,
    agent: str,
    triples: list[tuple[str, dict[str, Any], int]],
    migrated_at: str,
) -> int:
    """Persist a translated event stream onto the v2 store."""
    written = 0
    for event_type, data, turn_index in triples:
        payload = dict(data)
        payload["migrated"] = True
        payload["migrated_at"] = migrated_at
        dest.append_event(
            agent,
            event_type,
            payload,
            turn_index=max(turn_index, 1),
            spawned_in_turn=max(turn_index, 1),
            branch_id=1,
        )
        written += 1
    return written


def _backfill_assistant_tool_call_content(
    triples: list[tuple[str, dict[str, Any], int]],
) -> list[tuple[str, dict[str, Any], int]]:
    """Copy the most recent ``text_chunk`` content into following
    ``assistant_tool_calls`` events on the same turn.

    v2 replay attaches tool calls to the preceding assistant message. Backfilling
    preserves the v1 assistant text when calls were emitted separately.
    """
    out: list[tuple[str, dict[str, Any], int]] = []
    last_text_per_turn: dict[int, str] = {}
    for etype, data, ti in triples:
        if etype == "text_chunk" and isinstance(data.get("content"), str):
            last_text_per_turn[ti] = last_text_per_turn.get(ti, "") + data["content"]
        if etype == "assistant_tool_calls" and not data.get("content"):
            text = last_text_per_turn.get(ti, "")
            if text:
                data = dict(data)
                data["content"] = text
        out.append((etype, data, ti))
    return out


def _highest_synthetic_event_id(dest: SessionStore, agent: str) -> int:
    """Return the largest ``event_id`` carrying ``synthetic: true``.

    The largest event identifier is used when no explicit synthetic marker exists.
    """
    last = 0
    for evt in dest.get_events(agent):
        eid = evt.get("event_id")
        if isinstance(eid, int) and eid > last:
            last = eid
    return last


def _copy_state(source: SessionStore, dest: SessionStore) -> None:
    """Copy every key in the ``state`` table verbatim."""
    for key_bytes in source.state.keys(limit=2**31 - 1):
        key = (
            key_bytes.decode("utf-8", errors="replace")
            if isinstance(key_bytes, bytes)
            else key_bytes
        )
        try:
            dest.state[key] = source.state[key_bytes]
        except Exception as e:
            logger.warning(
                "Migrator failed to copy state key",
                key=key,
                error=str(e),
                exc_info=True,
            )


def _copy_kvault_table(source_table: Any, dest_table: Any, label: str) -> None:
    """Copy every key from a KohakuVault table to its destination twin."""
    for key_bytes in source_table.keys(limit=2**31 - 1):
        key = (
            key_bytes.decode("utf-8", errors="replace")
            if isinstance(key_bytes, bytes)
            else key_bytes
        )
        try:
            dest_table[key] = source_table[key_bytes]
        except Exception as e:
            logger.warning(
                f"Migrator failed to copy {label} key",
                key=key,
                error=str(e),
                exc_info=True,
            )


def _copy_meta_fields(source: SessionStore, dest: SessionStore) -> dict[str, Any]:
    """Copy every meta key from source to dest; return the loaded source meta."""
    source_meta: dict[str, Any] = {}
    for key_bytes in source.meta.keys(limit=2**31 - 1):
        key = (
            key_bytes.decode("utf-8", errors="replace")
            if isinstance(key_bytes, bytes)
            else key_bytes
        )
        try:
            value = source.meta[key_bytes]
        except Exception as e:
            logger.warning(
                "Migrator failed to read meta key",
                key=key,
                error=str(e),
                exc_info=True,
            )
            continue
        source_meta[key] = value
        if key == "format_version":
            continue
        try:
            dest.meta[key] = value
        except Exception as e:
            logger.warning(
                "Migrator failed to write meta key",
                key=key,
                error=str(e),
                exc_info=True,
            )
    return source_meta


def _copy_artifacts(source_path: Path, dest_path: Path) -> None:
    """Copy the ``<stem>.artifacts/`` directory if present."""
    source_art = source_path.parent / f"{source_path.stem}.artifacts"
    if not source_art.exists():
        return
    dest_art = artifacts_dir_for(dest_path)
    for item in source_art.iterdir():
        target = dest_art / item.name
        if item.is_dir():
            if target.exists():
                continue
            shutil.copytree(item, target)
        else:
            if target.exists():
                continue
            shutil.copy2(item, target)


def migrate(source_path: str, target_path: str) -> None:
    """Produce a v2 session at ``target_path`` from the v1 ``source_path``.

    The v1 event log is authoritative; snapshots are fallback input and a resume
    cache.
    """
    src_path = Path(source_path)
    dst_path = Path(target_path)
    if not src_path.exists():
        raise FileNotFoundError(src_path)
    if dst_path.exists():
        raise FileExistsError(dst_path)

    migrated_at = datetime.now(timezone.utc).isoformat()

    source = SessionStore(str(src_path))
    try:
        dest = SessionStore(str(dst_path))
        try:
            source_meta = _copy_meta_fields(source, dest)
            dest.meta["format_version"] = 2

            lineage = {
                "migrated_from": str(src_path),
                "source_version": int(source_meta.get("format_version", 1) or 1),
                "migrated_at": migrated_at,
                "migrator": "v1_to_v2",
            }
            dest.meta["migrated_from"] = lineage
            existing_lineage = source_meta.get("lineage") or {}
            merged = (
                dict(existing_lineage) if isinstance(existing_lineage, dict) else {}
            )
            merged["migration"] = lineage
            dest.meta["lineage"] = merged

            agents = _iter_agents(source, source_meta)

            for agent in agents:
                v1_events = source.get_events(agent)
                snapshot = source.load_conversation(agent) or []

                if v1_events:
                    triples = _translate_v1_events(v1_events)
                    used_path = "events"
                else:
                    triples = _synth_events_from_snapshot(snapshot)
                    used_path = "snapshot"

                # System prompts existed only in snapshots and must seed event replay.
                if snapshot and (snapshot[0].get("role") or "").lower() == "system":
                    sys_evt = (
                        "system_prompt_set",
                        {"content": snapshot[0].get("content", "")},
                        0,
                    )
                    triples = [sys_evt, *triples]

                # Tool announcements inherit same-turn assistant text for faithful replay.
                triples = _backfill_assistant_tool_call_content(triples)

                written = _write_migrated_events(dest, agent, triples, migrated_at)

                if snapshot:
                    dest.save_conversation(agent, snapshot)

                # The snapshot watermark must match the destination event stream.
                try:
                    dest.state[f"{agent}:snapshot_event_id"] = (
                        _highest_synthetic_event_id(dest, agent)
                    )
                except Exception as e:
                    logger.warning(
                        "Migrator failed to stamp snapshot_event_id",
                        agent=agent,
                        error=str(e),
                        exc_info=True,
                    )

                logger.info(
                    "Migrated agent history",
                    agent=agent,
                    events=written,
                    source=used_path,
                )

            _copy_state(source, dest)
            _copy_kvault_table(source.channels, dest.channels, "channel")
            _copy_kvault_table(source.subagents, dest.subagents, "subagent")
            _copy_kvault_table(source.jobs, dest.jobs, "job")
            dest.flush()
        finally:
            dest.close(update_status=False)
    finally:
        source.close(update_status=False)

    _copy_artifacts(src_path, dst_path)
