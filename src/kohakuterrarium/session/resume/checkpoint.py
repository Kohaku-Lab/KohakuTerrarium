"""Select a clean ordinary-turn checkpoint without reading event payloads."""

from typing import Any

from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.core.conversation.continuation import load_persisted_branch_view
from kohakuterrarium.session.store.events.counters import (
    EXACT_EVENT_ID_KEY,
    MAX_EVENT_ID_KEY,
)
from kohakuterrarium.session.store.records.resume_point import VERSION, text_messages
from kohakuterrarium.session.store.records.snapshot import branch_path, load_records


def prepare_clean_checkpoint(agent: Any, store: Any, namespace: str) -> dict | None:
    """Return a supported empty-tail checkpoint, or leave legacy restoration intact."""
    point = getattr(store, "_resume_point", None)
    if (
        not isinstance(point, dict)
        or type(point.get("version")) is not int
        or point["version"] != VERSION
    ):
        return None
    if store._resume_epoch != store._branch_maxima.invalidation_epoch:
        return None
    router = agent.output_router
    if (
        type(router.default_output) is not NoneOutput
        or router.named_outputs
        or router._secondary_outputs
    ):
        return None
    config = agent.config
    if any(
        (
            config.plugins,
            config.default_plugins,
            config.triggers,
            config.startup_trigger,
            config.mcp_servers,
            config.output_wiring,
        )
    ):
        return None
    if (
        point.get("namespace") != namespace
        or point.get("event_seq") != store._event_seq
    ):
        return None
    if set(store._event_seq) != {namespace}:
        return None
    if store.meta.get("config_type") != "agent" or store.meta.get("agents") != [
        namespace
    ]:
        return None
    watermark = point.get("watermark")
    closed_id = point.get("closed_event_id")
    if (
        type(watermark) is not int
        or type(closed_id) is not int
        or watermark > closed_id
    ):
        return None
    if closed_id != store.global_max_event_id:
        return None
    if (
        store.state.get(EXACT_EVENT_ID_KEY) != closed_id
        or store.state.get(MAX_EVENT_ID_KEY) != closed_id
    ):
        return None
    if store.state.get(f"{namespace}:open_text") or store.load_triggers(namespace):
        return None
    path = point.get("path")
    if not isinstance(path, list) or not path:
        return None
    if branch_path(store.state.get(f"{namespace}:snapshot_branch")) != path:
        return None
    selected = load_persisted_branch_view(store, namespace)
    if selected is None:
        if store.state.get(f"{namespace}:branch_view"):
            return None
        selected = dict(path)
    if sorted([t, b] for t, b in selected.items()) != path:
        return None
    if type(point.get("max_turn")) is not int or point["max_turn"] != path[-1][0]:
        return None
    records = load_records(store, namespace)
    record = next(
        (r for r in records if r["path"] == path and r["watermark"] == watermark), None
    )
    if (
        record is None
        or record.get("context_cutoff_event_id", 0)
        or record["compact_state"]["compact_count"]
    ):
        return None
    if not text_messages(record["messages"]):
        return None
    store._branch_maxima.restore_upper_turn(namespace, point["max_turn"])
    return {**record, "selected": selected}
