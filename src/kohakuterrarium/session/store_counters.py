"""Counter restoration helpers for ``SessionStore``.

Extracted from ``store.py`` so the main store module stays under the
600-line soft cap. These functions scan KVault tables after reopen and
rebuild the sequence counters that ``append_event`` and friends rely
on.
"""

from kohakuvault import KVault

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _decode_key(key_bytes: bytes | str) -> str:
    """Decode a KVault key to str."""
    if isinstance(key_bytes, bytes):
        return key_bytes.decode("utf-8", errors="replace")
    return key_bytes


def restore_event_counters(events: KVault, event_seq: dict[str, int]) -> int:
    """Scan the events table. Populate per-agent seq + return max event_id.

    ``event_seq`` is mutated in-place. Keys follow ``{agent}:e{seq:06d}``;
    event bodies may carry a Wave B ``event_id`` integer which we track
    so the global counter survives reopen.
    """
    max_event_id = 0
    for key_bytes in events.keys():
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
            logger.debug(
                "Failed to read event for id scan",
                error=str(e),
                exc_info=True,
            )
    return max_event_id


def restore_suffix_counters(table: KVault, sep: str, counter: dict[str, int]) -> None:
    """Restore ``{prefix}{sep}{seq:06d}``-shaped counters (e.g. channels).

    ``counter`` is mutated in-place.
    """
    for key_bytes in table.keys():
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

    Keys follow ``{parent}:{name}:{run}:meta``. ``runs`` is mutated
    in-place.
    """
    for key_bytes in subagents.keys():
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
