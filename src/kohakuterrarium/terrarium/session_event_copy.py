"""Copy one session store's events into another for graph split / merge."""

from kohakuterrarium.session.store import SessionStore


def _comparable_event(event: dict) -> dict:
    """Event payload as ``append_event`` would store it, minus ``event_id``.

    A copy gains ``spawned_in_turn = turn_index`` when the original predates
    that field, so both sides are normalised before prefix comparison.
    """
    payload = {k: v for k, v in event.items() if k != "event_id"}
    turn_index = payload.get("turn_index")
    if turn_index is not None and "spawned_in_turn" not in payload:
        payload["spawned_in_turn"] = turn_index
    return payload


def copy_events_into(src: SessionStore, dst: SessionStore) -> int:
    """Append events beyond the shared prefix, assigning destination event IDs."""
    # This synchronous topology transaction includes all accepted writes.
    for store in (src, dst):
        store.submit(lambda: None).result()
    try:
        src.events.flush_cache()
    except Exception:
        pass
    n = 0
    for agent in src.discover_agents_from_events():
        incoming = src.get_events(agent)
        shared = 0
        for old, new in zip(dst.get_events(agent), incoming):
            if _comparable_event(old) != _comparable_event(new):
                break
            shared += 1
        for raw in incoming[shared:]:
            data = dict(raw)
            event_type = data.pop("type", "event")
            # ``append_event`` sets event_id itself; clear any stale id.
            data.pop("event_id", None)
            kwargs = {}
            for fld in ("turn_index", "spawned_in_turn", "branch_id"):
                if fld in data:
                    kwargs[fld] = data.pop(fld)
            if "parent_branch_path" in data:
                pbp = data.pop("parent_branch_path")
                if isinstance(pbp, list):
                    kwargs["parent_branch_path"] = [tuple(p) for p in pbp]
            dst.append_event(agent, event_type, data, **kwargs)
            n += 1
    return n
