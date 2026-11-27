"""Attach physical Store keys to Memory's ordinary event projection."""

from kohakuterrarium.session.vault.io import key_text


class SourcedEvent(dict):
    """A Store event with its physical key."""

    def __init__(self, data: dict, source_ref: str):
        super().__init__(data)
        self.source_ref = source_ref


def sourced_events(store, agent: str, *, strict: bool = False) -> list[dict]:
    """Read indexed events with physical provenance, independent of lineage proof."""
    sourced = []
    for key, event in store._event_rows(agent, strict=strict):
        ref = key_text(key)
        if (
            not isinstance(ref, str)
            or (isinstance(key, bytes) and ref.encode("utf-8") != key)
            or not isinstance(event, dict)
        ):
            if strict:
                raise ValueError("Unrepresentable physical event row")
            sourced.append(event)
            continue
        sourced.append(SourcedEvent(event, ref))
    return sourced
