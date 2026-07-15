"""Per-graph drive repository and manager partitioning.

Each graph owns an isolated repository, manager, and dispatcher. Drives are
therefore invisible across disconnected graphs, while topology merge and split
move rows explicitly through repository export and import rather than sharing
mutable storage.

Repository selection prefers an explicit graph-scoped provider, then the graph's
session store, then ephemeral memory. A single repository instance can be
claimed by only one graph. The registry owns wiring and lifecycle only; drive
semantics remain in the manager and policy layers.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.manager import DriveManager
from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository
from kohakuterrarium.terrarium.drive.sink import DriveDeliverySink, DriveObserver
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot
from kohakuterrarium.terrarium.drive.store import open_session_drive_repository
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _make_store_provider(store: Any) -> "Callable[[str], Any] | None":
    """Normalize drive-store configuration into a graph-scoped provider.

    ``None`` falls through to session or memory storage. Callables resolve per
    graph. A repository instance is claimed by the first graph only, preventing
    disconnected graphs from sharing one mutable store.
    """
    if store is None:
        return None
    if callable(store):
        return store
    claimed: dict[str, bool] = {"done": False}

    def _single(_gid: str) -> Any:
        if claimed["done"]:
            return None
        claimed["done"] = True
        return store

    return _single


class GraphDriveRegistry:
    """Own per-graph repositories, managers, and dispatcher lifecycle."""

    def __init__(
        self,
        *,
        engine: Any,
        config: DriveRuntimeConfig,
        get_snapshot: Callable[[], EnabledRegistrySnapshot],
        sink: DriveDeliverySink,
        observer: DriveObserver | None,
        clock: Callable[[], datetime],
        rng: Any,
        id_factory: Callable[[], str],
        store: Any = None,
    ) -> None:
        self._engine = engine
        self._config = config
        self._get_snapshot = get_snapshot
        self._sink = sink
        self._observer = observer
        self._clock = clock
        self._rng = rng
        self._id_factory = id_factory
        self._provider = _make_store_provider(store)
        # Provider resolution is stable per graph; caching absence also prevents
        # a single-store provider from being consumed by later graphs.
        self._provider_repos: dict[str, Any] = {}
        self._managers: dict[str, DriveManager] = {}
        self._repos: dict[str, Any] = {}
        # Rebinding the same session store must not create another repository
        # connection.
        self._bound_stores: dict[str, Any] = {}
        # Managers may exist for tool access before restoration, but dispatch must
        # wait until resume finishes writing shared session state.
        self._ready_graphs: set[str] = set()
        self._active = False
        self._start_tasks: set[asyncio.Task] = set()
        # Detached manager stops are drained during shutdown rather than cancelled
        # mid-round.
        self._stop_tasks: set[asyncio.Task] = set()

    def peek(self, graph_id: str) -> DriveManager | None:
        """Return an existing graph manager without creating one."""
        return self._managers.get(graph_id)

    def all_managers(self) -> list[DriveManager]:
        """Return every live graph manager for cross-graph read unions."""
        return list(self._managers.values())

    def repository_for(self, graph_id: str) -> Any:
        return self._repos.get(graph_id)

    def durability_for(self, graph_id: str) -> str:
        """Return graph durability, or ephemeral before repository creation."""
        repo = self._repos.get(graph_id)
        return repo.durability if repo is not None else "ephemeral"

    @property
    def durability(self) -> str:
        """Summarize durability across all graph repositories.

        Matching graphs report their common mode, differing modes report
        ``mixed``, and an empty registry reports ``ephemeral``. Per-graph callers
        should use :meth:`durability_for`.
        """
        modes = {repo.durability for repo in self._repos.values()}
        if not modes:
            return "ephemeral"
        if len(modes) == 1:
            return next(iter(modes))
        return "mixed"

    def manager_for(self, graph_id: str, *, create: bool = True) -> DriveManager | None:
        """Get or create a graph manager using current storage precedence.

        First use resolves provider, attached session store, or memory storage.
        ``create=False`` performs a non-creating lookup.
        """
        existing = self._managers.get(graph_id)
        if existing is not None:
            return existing
        if not create:
            return None
        repo, store = self._resolve_repo(graph_id)
        return self._install(graph_id, repo, store)

    async def ensure_started(self, graph_id: str) -> DriveManager:
        """Mark a restored graph delivery-ready and start its dispatcher.

        Repeated calls are safe. Topology movement also uses this path because it
        operates on graphs that already crossed restoration.
        """
        self._ready_graphs.add(graph_id)
        manager = self.manager_for(graph_id)
        assert manager is not None
        await manager.start()
        return manager

    async def bind_store(self, graph_id: str, session_store: Any) -> DriveManager:
        """Bind a graph to session-backed storage before restoration completes.

        If early tool use already created an ephemeral manager, its rows migrate
        into the session repository. Rebinding the same store is idempotent.
        """
        if self._bound_stores.get(graph_id) is session_store:
            return self._managers[graph_id]
        # Explicit providers outrank session storage, and cached resolution avoids
        # consuming a single-store provider during precedence checks.
        if self._provider_repo(graph_id) is not None:
            return self.manager_for(graph_id)
        repo = open_session_drive_repository(session_store)
        existing = self._managers.get(graph_id)
        if existing is None:
            # Dispatch remains dormant until restoration finishes, avoiding
            # contention with resume writes.
            return self._install(graph_id, repo, session_store)
        await self._rebind(graph_id, existing, repo, session_store)
        return self._managers[graph_id]

    def rebind_repository(self, graph_id: str, repo: Any, store: Any = None) -> None:
        """Rewire a graph after topology movement has replaced its repository.

        The caller has already moved rows and closed the old repository. Dispatcher
        stop is asynchronous because the surviving store remains open and topology
        mutation should not wait for teardown. When a store itself will close,
        :meth:`detach_and_stop` must be used instead.
        """
        existing = self._managers.get(graph_id)
        if existing is not None and self._repos.get(graph_id) is repo:
            return
        if existing is not None:
            self._detach(graph_id, existing)
        self._install(graph_id, repo, store)

    def drop_graph(self, graph_id: str) -> None:
        """Forget an empty graph and schedule asynchronous manager shutdown.

        Merge and split use :meth:`detach_and_stop` when repository closure follows
        immediately.
        """
        manager = self._managers.pop(graph_id, None)
        self._repos.pop(graph_id, None)
        self._bound_stores.pop(graph_id, None)
        self._ready_graphs.discard(graph_id)
        if manager is not None:
            self._schedule_stop(manager)

    async def detach_and_stop(self, graph_id: str) -> None:
        """Stop and forget a graph manager while its repository is still open.

        Awaiting shutdown lets the dispatcher release claims before session-store
        replacement or topology movement closes the companion repository.
        """
        manager = self._managers.pop(graph_id, None)
        self._repos.pop(graph_id, None)
        self._bound_stores.pop(graph_id, None)
        self._ready_graphs.discard(graph_id)
        if manager is not None:
            try:
                await manager.stop()
            except Exception as exc:  # pragma: no cover
                logger.warning("drive manager detach stop failed", error=str(exc))

    async def start_all(self) -> None:
        """Activate the registry and start dispatchers for restored graphs only."""
        self._active = True
        for graph_id, manager in list(self._managers.items()):
            if graph_id in self._ready_graphs:
                await manager.start()

    async def stop_all(self) -> None:
        """Stop all dispatchers without leaving detached shutdowns mid-round.

        Pending starts are cancelled, while already scheduled stops are drained
        before active managers stop. This prevents dispatch from racing repository
        closure as the event loop shuts down.
        """
        for task in list(self._start_tasks):
            if not task.done():
                task.cancel()
        self._start_tasks.clear()
        stop_tasks = list(self._stop_tasks)
        self._stop_tasks.clear()
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        for manager in list(self._managers.values()):
            await manager.stop()
        self._active = False

    def set_snapshot(self, snapshot: EnabledRegistrySnapshot | None) -> None:
        """Publish registry changes to every graph manager."""
        for manager in self._managers.values():
            manager.set_snapshot(snapshot)

    def _provider_repo(self, graph_id: str) -> Any:
        """Resolve a graph's explicit provider once, including an absent result."""
        if graph_id in self._provider_repos:
            return self._provider_repos[graph_id]
        repo = self._provider(graph_id) if self._provider is not None else None
        self._provider_repos[graph_id] = repo
        return repo

    def _resolve_repo(self, graph_id: str) -> tuple[Any, Any]:
        """Resolve repository and backing session store by configured precedence."""
        provided = self._provider_repo(graph_id)
        if provided is not None:
            return provided, None
        stores = getattr(self._engine, "_session_stores", {})
        store = stores.get(graph_id)
        if store is not None:
            return open_session_drive_repository(store), store
        return MemoryDriveRepository(), None

    def resolve_destination_repo(self, graph_id: str) -> tuple[Any, Any]:
        """Resolve topology-movement storage with standard graph precedence."""
        return self._resolve_repo(graph_id)

    def _build_manager(self, graph_id: str, repo: Any) -> DriveManager:
        return DriveManager(
            repo,
            self._get_snapshot(),
            self._config,
            self._sink,
            observer=self._observer,
            clock=self._clock,
            rng=self._rng,
            id_factory=self._id_factory,
            topology_validator=self._make_topology_validator(graph_id),
        )

    def _make_topology_validator(self, graph_id: str) -> Callable[[Any], bool]:
        """Build a membership validator that tolerates incomplete topology state."""
        engine = self._engine

        def _valid(assignment: Any) -> bool:
            topo = getattr(engine, "_topology", None)
            graphs = getattr(topo, "graphs", None) if topo is not None else None
            if not isinstance(graphs, dict):
                return True
            graph = graphs.get(graph_id)
            if graph is None:
                return True
            if assignment.assignee_graph_id != graph_id:
                return False
            return assignment.assignee_creature_id in set(graph.creature_ids)

        return _valid

    def _install(self, graph_id: str, repo: Any, store: Any) -> DriveManager:
        manager = self._build_manager(graph_id, repo)
        self._managers[graph_id] = manager
        self._repos[graph_id] = repo
        if store is not None:
            self._bound_stores[graph_id] = store
        self._maybe_start(graph_id, manager)
        return manager

    def _detach(self, graph_id: str, manager: DriveManager) -> None:
        self._managers.pop(graph_id, None)
        self._repos.pop(graph_id, None)
        self._bound_stores.pop(graph_id, None)
        self._ready_graphs.discard(graph_id)
        self._schedule_stop(manager)

    async def _rebind(
        self, graph_id: str, existing: DriveManager, repo: Any, store: Any
    ) -> None:
        """Promote ephemeral rows into session storage before replacing a manager."""
        old_repo = self._repos.get(graph_id)
        if getattr(old_repo, "durability", "") == "ephemeral":
            try:
                payload = await old_repo.export_rows()
                if payload.get("drives"):
                    await repo.import_rows(payload)
            except Exception as exc:  # pragma: no cover
                logger.warning("drive repo rebind migrate failed", error=str(exc))
        await existing.stop()
        self._install(graph_id, repo, store)

    def _maybe_start(self, graph_id: str, manager: DriveManager) -> None:
        """Start a lazily created manager only after its graph is restoration-ready."""
        if not self._active or graph_id not in self._ready_graphs:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        task = asyncio.ensure_future(manager.start())
        self._start_tasks.add(task)
        task.add_done_callback(self._start_tasks.discard)

    def _schedule_stop(self, manager: DriveManager) -> None:
        """Schedule shutdown after detaching a manager without closing its store."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        task = asyncio.ensure_future(manager.stop())
        self._stop_tasks.add(task)
        task.add_done_callback(self._stop_tasks.discard)


__all__ = ["GraphDriveRegistry"]
