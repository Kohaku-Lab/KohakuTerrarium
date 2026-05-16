"""Worker-side auto-attach of SessionStore + SessionEventTee.

When a creature spawns on a worker (via the ``terrarium.runtime``
adapter's ``add_creature``), it should automatically:

1. Get a :class:`SessionStore` attached to its agent so events
   persist to the worker's local session dir.
2. Get a :class:`SessionEventTee` mirroring those events back to the
   controller's :class:`SessionMirrorWriter`.

The standalone worker engine has no Studio layer — the helper here
fills that role with a focused worker-side equivalent.  Without it,
remote-spawned creatures have NO persistence and the controller's
mirror is empty even though the wiring is in place.

Lifecycle ownership:

- One :class:`WorkerSessionAttacher` per worker engine.  It tracks
  per-creature SessionStore + Tee pairs.
- ``attach(creature_id)`` is called from
  :class:`TerrariumRuntimeAdapter` after a successful
  ``engine.add_creature``.  Reuses an existing store for the graph if
  one is already attached (multi-creature graphs share a store).
- ``detach(creature_id)`` is called from
  :class:`TerrariumRuntimeAdapter` on ``remove_creature``.  Closes the
  Tee; the SessionStore stays open so resume on the controller side
  still works.
- ``close_all()`` releases everything on adapter detach.
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
    """Resolve the worker's session dir fresh, honouring KT_CONFIG_DIR.

    Module-constant lookup at import time was the pollution source.
    """
    return config_dir() / "sessions"


# Back-compat — display only; live reads use ``_default_worker_session_dir``.
DEFAULT_WORKER_SESSION_DIR = Path.home() / ".kohakuterrarium" / "sessions"


class _ObservingSessionStores(dict):
    """A ``dict`` that notifies listeners when a new store is registered.

    Used to bridge ``engine.adopt_session`` (which mutates
    ``engine._session_stores`` directly) to
    :class:`WorkerSessionAttacher` so the resumed graph gets a Tee
    installed without routing through ``add_creature``.
    """

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
        # ONE Tee per graph (SessionStore is graph-scoped).  Each
        # creature gets its own bookkeeping entry pointing at the
        # graph's shared Tee so we know when the last creature has
        # detached and the Tee can be torn down.  Earlier versions
        # kept one Tee per creature and subscribed all of them to the
        # same SessionStore, which duplicated every event N times in
        # the controller mirror (one per creature in the graph).
        self._graph_tees: dict[str, SessionEventTee] = {}
        self._graph_refs: dict[str, set[str]] = {}
        # Resume on the worker (``terrarium.session/resume``) calls
        # ``engine.adopt_session`` directly — it doesn't route through
        # ``TerrariumRuntimeAdapter.add_creature``, so the per-creature
        # ``attach()`` hook never fires.  Wrap ``engine._session_stores``
        # with an observing dict that notifies us when adopt_session
        # registers the resumed graph's store, so we can install a Tee.
        self._wrap_engine_session_stores()

    def _wrap_engine_session_stores(self) -> None:
        """Replace ``engine._session_stores`` with an observing dict.

        Idempotent — if a previous attacher already wrapped the dict we
        chain onto its listener list instead of double-wrapping.
        """
        existing = getattr(self._engine, "_session_stores", None)
        if isinstance(existing, _ObservingSessionStores):
            existing._listeners.append(self._on_store_registered)
            return
        observing = _ObservingSessionStores(existing or {})
        observing._listeners.append(self._on_store_registered)
        self._engine._session_stores = observing

    def _on_store_registered(self, graph_id: str, store: SessionStore) -> None:
        """Install a Tee for a graph that was registered out-of-band
        (e.g. via ``engine.adopt_session`` on the resume code path).
        Idempotent — if a Tee already exists for this graph, we leave
        it alone.
        """
        if graph_id in self._graph_tees:
            return
        try:
            tee = SessionEventTee(graph_id, store, self._node)
            tee.attach()
        except RuntimeError:
            # No running event loop yet — the resume path will end up
            # calling attach() per-creature via the normal hook when
            # the runtime starts iterating creatures, OR we install on
            # the next add_creature.  Don't crash adopt_session.
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
        """Attach a SessionStore + Tee for ``creature_id``.

        Idempotent — re-attaching the same creature is a no-op.  When
        multiple creatures share a graph they also share the graph's
        single SessionEventTee, so every event reaches the controller's
        mirror exactly once.
        """
        try:
            creature = self._engine.get_creature(creature_id)
        except KeyError:
            logger.debug(
                "auto-session-attach: creature %r not found on engine; skip",
                creature_id,
            )
            return

        graph_id = creature.graph_id
        # Reuse the engine-attached store for this graph if present;
        # else mint one at the worker's session dir.  Direct attach
        # via the engine's _session_stores dict mirrors what
        # studio/sessions/lifecycle.attach_session_store_for_creature
        # does — the worker doesn't run studio code so we replicate
        # the bookkeeping here.
        store = self._engine._session_stores.get(graph_id)
        # CRITICAL ORDERING: ``_ObservingSessionStores`` fires
        # ``_on_store_registered`` on assignment, which installs a Tee
        # immediately — and Tee.attach() snapshots ``store.load_meta()``
        # synchronously into the outbound queue.  If we registered the
        # store BEFORE writing meta, the host mirror would receive a
        # meta snapshot with only ``agents`` (the load_meta default),
        # losing ``config_path`` / ``config_snapshot`` and breaking
        # resume.  Always populate meta on a freshly-minted store before
        # publishing it via the engine's dict.
        if store is None:
            path = self._session_dir / f"{graph_id}.kohakutr"
            store = SessionStore(str(path))
            # A worker engine has no Studio layer to call ``init_meta``,
            # so do it here BEFORE the observer fires.  Mirror what
            # ``lifecycle.attach_session_store_for_creature`` does.
            self._ensure_store_meta(store, graph_id, creature)
            self._engine._session_stores[graph_id] = store
        else:
            # Existing store — just top up agents.
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
        """Make sure the worker store carries resumable meta.

        First creature in the graph → ``init_meta`` from its config;
        subsequent creatures sharing the store → append to
        ``meta["agents"]``. If the store was already inited (e.g. the
        engine attached + inited it) this only tops up the agents list.
        """
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
        # Capture a config_snapshot so resume on this worker (and on any
        # other node the .kohakutr is later pushed to) can rebuild the
        # agent without re-reading a folder that may not exist there.
        # ``agent_path`` is empty for inline-config spawns (recipe-root,
        # SDK-built AgentConfig) — without the snapshot, resume_agent
        # raises "Session has no config_path in metadata".
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
        """Detach the Tee for ``creature_id``.

        The Tee is shared across every creature in the graph; only
        tear it down once the last creature in the graph detaches.
        Keeps the store open so the controller mirror can keep
        replaying.
        """
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

    def close_all(self) -> None:
        """Detach every tracked Tee.  Idempotent."""
        for tee in list(self._graph_tees.values()):
            tee.detach()
        self._graph_tees.clear()
        self._graph_refs.clear()


__all__ = ["DEFAULT_WORKER_SESSION_DIR", "WorkerSessionAttacher"]
