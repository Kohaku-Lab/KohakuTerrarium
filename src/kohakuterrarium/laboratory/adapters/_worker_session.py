"""Persist worker sessions locally and mirror their events to the controller.

A standalone worker has no Studio lifecycle layer, so this module attaches a
shared store and event tee per graph. Creature references keep the tee alive
until the graph's last creature detaches; stores remain available for resume.
"""

from pathlib import Path

from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.laboratory.protocols import LabNotifier
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.sync import SessionEventTee
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _default_worker_session_dir() -> Path:
    """Resolve the worker session directory using the current configuration."""
    return config_dir() / "sessions"


# Retained for display compatibility; live paths honor current configuration.
DEFAULT_WORKER_SESSION_DIR = Path.home() / ".kohakuterrarium" / "sessions"


class _ObservingSessionStores(dict):
    """Notify listeners when a session store is first registered."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._listeners: list = []

    def __setitem__(self, key, value) -> None:
        new = key not in self
        super().__setitem__(key, value)
        if new:
            for cb in list(self._listeners):
                try:
                    cb(key, value)
                except Exception:  # pragma: no cover - defensive
                    logger.exception(
                        "observing session-stores listener failed for %r", key
                    )


class WorkerSessionAttacher:
    """Tracks SessionStore + Tee pairs for one worker engine."""

    def __init__(
        self,
        engine: Terrarium,
        lab_node: LabNotifier,
        *,
        session_dir: str | Path | None = None,
    ) -> None:
        self._engine = engine
        self._node = lab_node
        self._session_dir = Path(session_dir or _default_worker_session_dir())
        self._session_dir.mkdir(parents=True, exist_ok=True)
        # Stores and tees are graph-scoped. Per-creature references prevent
        # duplicate subscriptions and identify when the shared tee can close.
        self._graph_tees: dict[str, SessionEventTee] = {}
        self._graph_refs: dict[str, set[str]] = {}
        # adopt_session bypasses the per-creature attach hook, so observe direct
        # store registration to install a tee for resumed graphs.
        self._wrap_engine_session_stores()

    def _wrap_engine_session_stores(self) -> None:
        """Observe store registration without wrapping an existing observer twice."""
        existing = getattr(self._engine, "_session_stores", None)
        if isinstance(existing, _ObservingSessionStores):
            existing._listeners.append(self._on_store_registered)
            return
        observing = _ObservingSessionStores(existing or {})
        observing._listeners.append(self._on_store_registered)
        self._engine._session_stores = observing

    def _on_store_registered(self, graph_id: str, store: SessionStore) -> None:
        """Install a tee for a newly registered graph if one is not active."""
        if graph_id in self._graph_tees:
            return
        try:
            tee = SessionEventTee(graph_id, store, self._node)
            tee.attach()
        except RuntimeError:
            # Registration may precede the event loop; a later creature attach
            # retries installation without failing session adoption.
            return
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "auto-session-attach: failed to install Tee for resumed graph %r",
                graph_id,
            )
            return
        self._graph_tees[graph_id] = tee
        self._graph_refs.setdefault(graph_id, set())

    def attach(self, creature_id: str) -> None:
        """Attach a creature to its graph's shared store and event tee."""
        try:
            creature = self._engine.get_creature(creature_id)
        except KeyError:
            logger.debug(
                "auto-session-attach: creature %r not found on engine; skip",
                creature_id,
            )
            return

        graph_id = creature.graph_id
        # Workers lack Studio's session lifecycle, so reproduce its graph-store
        # bookkeeping while reusing any store already attached by the engine.
        store = self._engine._session_stores.get(graph_id)
        # Populate metadata before publishing a new store: publication installs
        # the tee, which immediately snapshots metadata for the controller mirror.
        if store is None:
            path = self._session_dir / f"{graph_id}.kohakutr"
            store = SessionStore(str(path), writer_lock=True)
            # Initialize metadata here because no Studio lifecycle runs on workers.
            self._ensure_store_meta(store, graph_id, creature)
            self._engine._session_stores[graph_id] = store
        else:

            self._ensure_store_meta(store, graph_id, creature)
        try:
            creature.agent.attach_session_store(store)
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "auto-session-attach: agent.attach_session_store failed for %r",
                creature_id,
            )
            return

        if graph_id not in self._graph_tees:
            tee = SessionEventTee(graph_id, store, self._node)
            tee.attach()
            self._graph_tees[graph_id] = tee
        self._graph_refs.setdefault(graph_id, set()).add(creature_id)

    def _ensure_store_meta(self, store: SessionStore, graph_id: str, creature) -> None:
        """Initialize resumable metadata or add the creature to its agent list."""
        agent = getattr(creature, "agent", None)
        cfg = getattr(agent, "config", None)
        name = getattr(cfg, "name", None) or creature.creature_id
        try:
            meta = store.load_meta()
        except Exception:  # pragma: no cover - defensive
            meta = {}
        if meta.get("config_type"):
            agents = list(meta.get("agents") or [])
            if name not in agents:
                agents.append(name)
                store.meta["agents"] = agents
            return
        config_path = str(getattr(cfg, "agent_path", "") or "")
        pwd = str(getattr(getattr(agent, "executor", None), "_working_dir", "") or "")
        # A portable snapshot lets any worker resume inline configurations or
        # configurations whose original directory is unavailable there.
        snapshot: dict = {}
        if isinstance(cfg, AgentConfig):
            try:
                snapshot = pack_agent_config(cfg)
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "auto-session-attach: pack_agent_config failed for graph %r",
                    graph_id,
                )
                snapshot = {}
        try:
            store.init_meta(
                session_id=graph_id,
                config_type="agent",
                config_path=config_path,
                pwd=pwd,
                agents=[name],
                config_snapshot=snapshot,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "auto-session-attach: init_meta failed for graph %r", graph_id
            )

    def detach(self, creature_id: str) -> None:
        """Release a creature reference and close the tee when its graph is unused."""
        for graph_id, refs in list(self._graph_refs.items()):
            if creature_id not in refs:
                continue
            refs.discard(creature_id)
            if not refs:
                tee = self._graph_tees.pop(graph_id, None)
                self._graph_refs.pop(graph_id, None)
                if tee is not None:
                    tee.detach()
            return

    def discard_graph(self, graph_id: str) -> None:
        """Detach all mirroring state before a recipe store is deleted."""
        tee = self._graph_tees.pop(graph_id, None)
        self._graph_refs.pop(graph_id, None)
        if tee is not None:
            tee.detach()

    def close_all(self) -> None:
        """Detach every tracked event tee."""
        for tee in list(self._graph_tees.values()):
            tee.detach()
        self._graph_tees.clear()
        self._graph_refs.clear()


__all__ = ["DEFAULT_WORKER_SESSION_DIR", "WorkerSessionAttacher"]
