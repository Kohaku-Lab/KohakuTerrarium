"""Rebuild session sequence counters from persisted KVault keys."""

from kohakuvault import KVault

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Truncated key scans can restore stale counters and overwrite existing rows.
_KV_KEYS_LIMIT: int = 2**31 - 1


def _decode_key(key_bytes: bytes | str) -> str:
    """Decode a KVault key to str."""
    if isinstance(key_bytes, bytes):
        return key_bytes.decode("utf-8", errors="replace")
    return key_bytes


def restore_event_counters(events: KVault, event_seq: dict[str, int]) -> int:
    """Restore per-agent event sequences and return the largest event ID.

    ``event_seq`` is updated in place from ``{agent}:e{seq}`` keys.
    """
    max_event_id = 0
    for key_bytes in events.keys(limit=_KV_KEYS_LIMIT):
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
        try:
            evt = events[key_bytes]
            if isinstance(evt, dict):
                eid = evt.get("event_id")
                if isinstance(eid, int) and eid > max_event_id:
                    max_event_id = eid
        except Exception as e:
            logger.warning(
                "Failed to read event for id scan",
                error=str(e),
                exc_info=True,
            )
    return max_event_id


def restore_suffix_counters(table: KVault, sep: str, counter: dict[str, int]) -> None:
    """Restore suffix-based sequence counters in place."""
    for key_bytes in table.keys(limit=_KV_KEYS_LIMIT):
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
    for key_bytes in subagents.keys(limit=_KV_KEYS_LIMIT):
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
