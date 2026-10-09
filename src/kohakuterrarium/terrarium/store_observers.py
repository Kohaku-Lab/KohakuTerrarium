"""Observe session stores as they join an engine.

``engine._session_stores`` is replaced by :class:`ObservingSessionStores`,
which calls each listener ``(graph_id, store)`` whenever a graph gets a store
it did not hold before or a different store than it held (graph merges and
splits re-key stores this way). Higher tiers (the Lab worker, Studio's
live-session list) subscribe without the engine knowing about them.
"""

from typing import Any, Callable

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

StoreListener = Callable[[str, Any], None]


class ObservingSessionStores(dict):
    """A ``graph_id → store`` dict that notifies listeners of new stores."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._listeners: list[StoreListener] = []

    def __setitem__(self, key, value) -> None:
        changed = self.get(key) is not value
        super().__setitem__(key, value)
        if not changed:
            return
        for listener in list(self._listeners):
            try:
                listener(key, value)
            except Exception:  # noqa: BLE001 - one listener must not break registration
                logger.exception("session-store listener failed for %r", key)


def observe_session_stores(
    engine: Any, listener: StoreListener
) -> ObservingSessionStores:
    """Subscribe ``listener`` to ``engine``'s stores; idempotent per listener."""
    existing = getattr(engine, "_session_stores", None)
    if isinstance(existing, ObservingSessionStores):
        observing = existing
    else:
        observing = ObservingSessionStores(existing or {})
        engine._session_stores = observing
    if listener not in observing._listeners:
        observing._listeners.append(listener)
    return observing
