"""Rebuild session sequence counters from persisted KVault keys."""

from dataclasses import dataclass

from kohakuvault import KVault

from kohakuterrarium.session.vault_io import read_prefix, scan_keys
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _decode_key(key_bytes: bytes | str) -> str:
    """Decode a KVault key to str."""
    if isinstance(key_bytes, bytes):
        return key_bytes.decode("utf-8", errors="replace")
    return key_bytes


# No event id ever assigned in the file exceeds this watermark.
MAX_EVENT_ID_KEY = "counters:max_event_id"
# Highest persisted event id at the last clean close; valid only while it
# equals the watermark.
EXACT_EVENT_ID_KEY = "counters:exact_event_id"


@dataclass(frozen=True, slots=True)
class EventIdCounters:
    """Persisted event-id state read at open.

    ``exact`` is the highest persisted id when the clean-close marker matches
    the watermark, else ``None`` (a scan is needed). ``floor`` is the watermark:
    new ids must be assigned above it.
    """

    exact: int | None
    floor: int


def _read_counter(state: KVault, key: str) -> int | None:
    try:
        value = state.get(key)
    except Exception as e:
        logger.warning(
            "Failed to read persisted event counter", error=str(e), exc_info=True
        )
        return None
    return value if isinstance(value, int) else None


def restore_event_counters(
    events: KVault,
    event_seq: dict[str, int],
    state: KVault | None = None,
) -> EventIdCounters:
    """Restore per-agent event sequences and read the persisted id counters.

    ``event_seq`` is updated in place from ``{agent}:e{seq}`` keys; event
    values are never read here.
    """
    _restore_event_seq_from_keys(events, event_seq)
    if state is None:
        return EventIdCounters(None, 0)
    watermark = _read_counter(state, MAX_EVENT_ID_KEY)
    exact = _read_counter(state, EXACT_EVENT_ID_KEY)
    if watermark is not None and exact == watermark:
        return EventIdCounters(exact, watermark)
    return EventIdCounters(None, watermark or 0)


def _restore_event_seq_from_keys(events: KVault, event_seq: dict[str, int]) -> None:
    for key_bytes in scan_keys(events):
        key = _decode_key(key_bytes)
        parts = key.rsplit(":e", 1)
        if len(parts) == 2:
            agent = parts[0]
            try:
                seq = int(parts[1])
                if agent not in event_seq or seq >= event_seq[agent]:
                    event_seq[agent] = seq + 1
            except ValueError:
                pass


def scan_max_event_id(events: KVault) -> int:
    """The largest ``event_id`` among persisted event values."""
    max_event_id = 0
    for _, evt in read_prefix(events, what="event for id scan"):
        if isinstance(evt, dict):
            eid = evt.get("event_id")
            if isinstance(eid, int) and eid > max_event_id:
                max_event_id = eid
    return max_event_id


def persist_event_counter(state: KVault, max_event_id: int, reserved: int) -> bool:
    """Record a clean close whose persisted maximum is ``max_event_id``.

    ``reserved`` is the watermark this writer reserved. When no other writer
    raised the watermark past it, the watermark drops to the exact maximum
    with the marker in one batch; otherwise only the marker is written, so it
    mismatches and the next open scans.
    """
    try:
        watermark = state.get(MAX_EVENT_ID_KEY)
        if not isinstance(watermark, int) or watermark <= max(reserved, max_event_id):
            state.put_many(
                [(MAX_EVENT_ID_KEY, max_event_id), (EXACT_EVENT_ID_KEY, max_event_id)]
            )
        else:
            state[EXACT_EVENT_ID_KEY] = max_event_id
        return True
    except Exception as e:
        logger.warning("Failed to persist event counter", error=str(e), exc_info=True)
        return False


def reserve_event_ids(state: KVault, upto: int) -> bool:
    """Raise the persisted watermark to at least ``upto``."""
    try:
        watermark = state.get(MAX_EVENT_ID_KEY)
        if not isinstance(watermark, int) or watermark < upto:
            state[MAX_EVENT_ID_KEY] = upto
        return True
    except Exception as e:
        logger.warning("Failed to reserve event ids", error=str(e), exc_info=True)
        return False


def restore_suffix_counters(table: KVault, sep: str, counter: dict[str, int]) -> None:
    """Restore suffix-based sequence counters in place."""
    for key_bytes in scan_keys(table):
        key = _decode_key(key_bytes)
        parts = key.rsplit(sep, 1)
        if len(parts) == 2:
            prefix = parts[0]
            try:
                seq = int(parts[1])
                if prefix not in counter or seq >= counter[prefix]:
                    counter[prefix] = seq + 1
            except ValueError:
                pass


def restore_subagent_counters(subagents: KVault, runs: dict[str, int]) -> None:
    """Restore per-(parent, name) sub-agent run counters.

    ``runs`` is updated in place from ``{parent}:{name}:{run}:meta`` keys.
    """
    for key_bytes in scan_keys(subagents):
        key = _decode_key(key_bytes)
        if key.endswith(":meta"):
            parts = key[: -len(":meta")].rsplit(":", 2)
            if len(parts) == 3:
                parent, name, run_str = parts
                sa_key = f"{parent}:{name}"
                try:
                    run = int(run_str)
                    if sa_key not in runs or run >= runs[sa_key]:
                        runs[sa_key] = run + 1
                except ValueError:
                    pass
