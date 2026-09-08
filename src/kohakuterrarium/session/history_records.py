"""Shared stream selection, response envelopes, and full history record reads."""

import re
from typing import Any

from kohakuterrarium.errors import ConflictError, NotFoundError
from kohakuterrarium.session.history_paging import (
    HistoryPagingError,
    _history_key,
    _record_fingerprint,
    _stream_identity,
    _unavailable_fingerprint,
    decode_cursor,
    page_channel_store,
    page_events,
    page_snapshot,
    physical_refs,
)


def history_page(
    store: Any,
    target: str,
    *,
    session_id: str,
    stream: str = "events",
    snapshot: list | None = None,
    envelope: dict | None = None,
    **kwargs,
) -> dict:
    """Select a stored stream and return its complete response envelope."""
    envelope = envelope or {}
    if target.startswith("ch:"):
        if stream not in ("events", "channel"):
            raise HistoryPagingError("channel target requires channel stream")
        page = page_channel_store(
            store, target[3:], session_id=session_id, envelope=envelope, **kwargs
        )
    else:
        if stream not in ("events", "snapshot"):
            raise HistoryPagingError("unsupported paged stream")
        initial = all(
            kwargs.get(key) is None for key in ("before", "after", "history_id")
        )
        if (
            stream == "events"
            and initial
            and not physical_refs(store.events, target, "e")
        ):
            stream = "snapshot"
        if stream == "snapshot":
            messages = (
                snapshot
                if snapshot is not None
                else store.load_conversation(target) or []
            )
            page = page_snapshot(
                messages,
                target=target,
                session_id=session_id,
                envelope=envelope,
                **kwargs,
            )
        else:
            page = page_events(
                store, target, session_id=session_id, envelope=envelope, **kwargs
            )
    items = page.pop("items")
    return {
        **envelope,
        **page,
        "events": items if page["history_page"]["stream"] == "events" else [],
        "messages": items if page["history_page"]["stream"] != "events" else [],
    }


def history_detail(
    store: Any,
    target: str,
    *,
    session_id: str,
    stream: str,
    ref: str,
    history_id: str,
    snapshot: list | None = None,
    channel_messages: list | None = None,
) -> dict:
    """Read a complete record after validating stream, target, and physical identity."""
    is_channel = target.startswith("ch:")
    if (is_channel and stream != "channel") or (
        not is_channel and stream not in ("events", "snapshot")
    ):
        raise HistoryPagingError("detail stream does not match target")
    name = target[3:] if is_channel else target
    cursor = decode_cursor(ref)
    if cursor.stream != stream or cursor.target != name:
        raise HistoryPagingError("detail reference does not match target or stream")
    identity = _stream_identity(session_id, stream, name)
    if stream == "snapshot":
        snapshot = (
            snapshot if snapshot is not None else store.load_conversation(name) or []
        )
        identity = _record_fingerprint([identity, _record_fingerprint(snapshot)])
    if history_id != identity or cursor.history_id != identity:
        raise ConflictError("history identity changed; reload history")
    indexed = stream == "snapshot" or channel_messages is not None
    pattern = (
        r"(?:0|[1-9][0-9]*)"
        if indexed
        else re.escape(name) + (r":e[0-9]+" if stream == "events" else r":m[0-9]+")
    )
    if re.fullmatch(pattern, cursor.ref) is None:
        raise HistoryPagingError("invalid physical detail reference")
    try:
        if indexed:
            messages = channel_messages if channel_messages is not None else snapshot
            if messages is None:
                messages = store.load_conversation(name) or []
            record = messages[int(cursor.ref)]
        else:
            vault = store.events if stream == "events" else store.channels
            vault.flush_cache()
            record = vault[cursor.ref]
    except (KeyError, IndexError, ValueError, OSError) as exc:
        raise NotFoundError("history record unavailable") from exc
    if not isinstance(record, dict):
        raise NotFoundError("history record unavailable")
    if (
        cursor.fingerprint != _unavailable_fingerprint(cursor.ref)
        and _record_fingerprint(record) != cursor.fingerprint
    ):
        raise ConflictError("history record changed; reload history")
    return {
        "record": {**record, "_history_key": _history_key(stream, name, cursor.ref)},
        "history_page": {"version": 1, "stream": stream, "history_id": identity},
    }
