"""Bounded physical history pages for event, channel, and snapshot streams."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.session.store import iter_kv_keys

DEFAULT_LIMIT = 400
MAX_LIMIT = 400
MAX_PAGE_BYTES = 4 * 1024 * 1024


class HistoryPagingError(ValueError):
    """Invalid history page or detail request."""


@dataclass(frozen=True, slots=True)
class PageCursor:
    """Opaque exclusive cursor bound to one physical record."""

    stream: str
    target: str
    ref: str
    fingerprint: str
    history_id: str = ""


def _canonical_json(record: Any) -> bytes:
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), default=str
    ).encode()


def _record_fingerprint(record: Any) -> str:
    return hashlib.sha256(_canonical_json(record)).hexdigest()[:16]


def _unavailable_fingerprint(ref: str) -> str:
    return "u:" + hashlib.sha256(ref.encode()).hexdigest()[:16]


def encode_cursor(cursor: PageCursor) -> str:
    """Encode an opaque, URL-safe exclusive record cursor."""
    payload = dict(
        v=1,
        s=cursor.stream,
        t=cursor.target,
        r=cursor.ref,
        f=cursor.fingerprint,
        h=cursor.history_id,
    )
    return base64.urlsafe_b64encode(_canonical_json(payload)).decode("ascii")


def decode_cursor(raw: str) -> PageCursor:
    """Decode a structurally valid cursor."""
    try:
        if not isinstance(raw, str) or len(raw) > 16384:
            raise ValueError("invalid size")
        payload = json.loads(
            base64.b64decode(raw.encode("ascii"), altchars=b"-_", validate=True)
        )
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError("invalid version")
        if not all(
            isinstance(payload.get(k), str) and payload[k] for k in ("s", "t", "r", "f")
        ):
            raise ValueError("invalid fields")
        if not isinstance(payload.get("h", ""), str):
            raise ValueError("invalid identity")
        return PageCursor(
            payload["s"], payload["t"], payload["r"], payload["f"], payload.get("h", "")
        )
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HistoryPagingError("malformed history cursor") from exc


def _stream_identity(session_id: str, stream: str, target: str) -> str:
    return hashlib.sha256(_canonical_json([session_id, stream, target])).hexdigest()[
        :16
    ]


def physical_refs(vault: Any, target: str, kind: str) -> list[str]:
    """Enumerate exact target records in numeric storage order, without values."""
    vault.flush_cache()
    prefix = f"{target}:{kind}"
    refs = []
    for key in iter_kv_keys(vault, prefix=prefix):
        ref = key.decode("utf-8") if isinstance(key, bytes) else key
        suffix = ref[len(prefix) :]
        if suffix and suffix.isascii() and suffix.isdigit():
            refs.append(ref)
    return sorted(refs, key=lambda ref: int(ref[len(prefix) :]))


def _ordered_event_refs(store: Any, agent: str) -> list[str]:
    return physical_refs(store.events, agent, "e")


def _history_key(stream: str, target: str, ref: str) -> str:
    return f"{stream}:{target}:{ref}"


def _try_get(get_record: Callable, ref: str) -> tuple[Any, str]:
    try:
        record = get_record(ref)
        if isinstance(record, dict):
            return record, _record_fingerprint(record)
    except Exception:
        pass
    return None, _unavailable_fingerprint(ref)


def _preview_value(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:2000] + "…"
    if isinstance(value, list):
        return [_preview_value(item, depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        return {
            str(k)[:100]: _preview_value(v, depth + 1)
            for k, v in list(value.items())[:20]
        }
    return value


def _bounded_preview(record: dict, detail: str, max_bytes: int) -> dict:
    """Produce a preview retaining physical identity within the byte budget."""
    markers = {
        "_history_key": record["_history_key"],
        "_history_detail": detail,
        "_history_truncated": True,
    }
    preview = {**_preview_value(record), **markers}
    if isinstance(preview.get("output"), str):
        preview["output_preview"] = preview["output"][:50]
    while len(_canonical_json(preview)) > min(4096, max_bytes):
        candidates = [k for k in preview if k not in markers]
        if not candidates:
            break
        key = max(candidates, key=lambda k: len(_canonical_json({k: preview[k]})))
        value = preview[key]
        if isinstance(value, str) and len(value) > 16:
            preview[key] = value[: len(value) // 2] + "…"
        else:
            del preview[key]
    return preview


def _clamp_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise HistoryPagingError("limit must be a positive integer")
    return min(limit, MAX_LIMIT)


def _page(
    refs: list[str],
    get_record: Callable,
    *,
    stream: str,
    target: str,
    session_id: str,
    limit: int = DEFAULT_LIMIT,
    before: str | None = None,
    after: str | None = None,
    history_id: str | None = None,
    is_processing: bool = False,
    live_job_ids: tuple[str, ...] = (),
    envelope: dict | None = None,
    revision: str | None = None,
) -> dict:
    limit = _clamp_limit(limit)
    if before is not None and after is not None:
        raise HistoryPagingError("before and after cursors are mutually exclusive")
    identity = _stream_identity(session_id, stream, target)
    if revision is not None:
        identity = _record_fingerprint([identity, revision])
    reset = history_id is not None and history_id != identity
    index = None
    if before is not None or after is not None:
        cursor = decode_cursor(before if before is not None else after)
        if cursor.stream != stream or cursor.target != target:
            raise HistoryPagingError("cursor stream or target does not match")
        if cursor.history_id and cursor.history_id != identity:
            reset = True
        if cursor.ref not in refs:
            reset = True
        else:
            index = refs.index(cursor.ref)
            _, fingerprint = _try_get(get_record, cursor.ref)
            reset |= fingerprint != cursor.fingerprint
    meta = dict(
        version=1,
        stream=stream,
        history_id=identity,
        before=None,
        after=None,
        has_older=False,
        has_newer=False,
        reset_required=reset,
    )
    result = dict(
        items=[],
        history_page=meta,
        is_processing=is_processing,
        live_job_ids=list(live_job_ids),
    )
    reserve = dict(result)
    longest = max(refs, key=lambda ref: len(_canonical_json(ref)), default="0")
    placeholder = encode_cursor(
        PageCursor(stream, target, longest, "u:" + "f" * 16, identity)
    )
    reserve["history_page"] = {**meta, "before": placeholder, "after": placeholder}
    if envelope is not None:
        reserve = {**reserve, **envelope, "events": [], "messages": []}
    budget = MAX_PAGE_BYTES - len(_canonical_json(reserve)) - limit
    if budget <= 0:
        raise HistoryPagingError("history metadata exceeds page byte budget")
    if reset:
        return result
    indexes = (
        range(index + 1, len(refs))
        if after is not None and index is not None
        else range((index if index is not None else len(refs)) - 1, -1, -1)
    )
    consumed = []
    total = 0
    for idx in indexes:
        if len(consumed) == limit:
            break
        ref = refs[idx]
        record, fingerprint = _try_get(get_record, ref)
        token = encode_cursor(PageCursor(stream, target, ref, fingerprint, identity))
        key = _history_key(stream, target, ref)
        if record is None:
            item = dict(
                _history_key=key, _history_unavailable=True, _history_detail=token
            )
        else:
            item = {**record, "_history_key": key}
            if len(_canonical_json(item)) > budget:
                item = _bounded_preview(item, token, budget)
        size = len(_canonical_json(item))
        if total + size > budget:
            if not consumed:
                raise HistoryPagingError("record identity exceeds page byte budget")
            break
        consumed.append((idx, token, item))
        total += size
    if after is None:
        consumed.reverse()
    if consumed:
        meta.update(
            before=consumed[0][1],
            after=consumed[-1][1],
            has_older=consumed[0][0] > 0,
            has_newer=consumed[-1][0] < len(refs) - 1,
        )
    result["items"] = [entry[2] for entry in consumed]
    return result


def page_events(store: Any, agent: str, *, session_id: str, **kwargs) -> dict:
    """Page an agent's exact physical event records."""
    return _page(
        _ordered_event_refs(store, agent),
        lambda ref: store.events[ref],
        stream="events",
        target=agent,
        session_id=session_id,
        **kwargs,
    )


def page_channel_store(store: Any, channel: str, *, session_id: str, **kwargs) -> dict:
    """Page channel records without loading the full message log."""
    return _page(
        physical_refs(store.channels, channel, "m"),
        lambda ref: store.channels[ref],
        stream="channel",
        target=channel,
        session_id=session_id,
        **kwargs,
    )


def page_channels(
    messages: list[dict], *, session_id: str, channel: str, **kwargs
) -> dict:
    """Page an already merged channel list in its existing order."""
    return _page(
        [str(i) for i in range(len(messages))],
        lambda ref: messages[int(ref)],
        stream="channel",
        target=channel,
        session_id=session_id,
        **kwargs,
    )


def page_snapshot(
    messages: list[dict], *, session_id: str, target: str, **kwargs
) -> dict:
    """Page a legacy snapshot with absolute-position record identities."""
    return _page(
        [str(i) for i in range(len(messages))],
        lambda ref: messages[int(ref)],
        stream="snapshot",
        target=target,
        session_id=session_id,
        revision=_record_fingerprint(messages),
        **kwargs,
    )
