"""Per-graph Drive repository + manager partitioning (design §3.1, §7.1; Phase F).

Phase E ran ONE :class:`DriveManager` over ONE repository for the whole engine.
But a Drive is graph-scoped (design §3.1): a Drive belongs to exactly one graph
and topology merge/split moves Drive rows *between* graph repositories rather
than sharing one mutable store across disconnected graphs (design §7.1). Phase F
therefore partitions the runtime — each graph gets its own repository and its own
``DriveManager`` + dispatcher, so a Drive created in graph A is invisible to
graph B's manager, and merge/split can move rows with the ``export_rows`` /
``import_rows`` seam.

Repository source precedence, per graph (design §7.1):

1. an explicit ``drive_store`` provider — a graph-scoped factory ``(gid) -> repo``
   (or a single bound repository claimed by the first graph, never blindly shared);
2. the graph's attached :class:`SessionStore` — durable, resumes after restart;
3. :class:`MemoryDriveRepository` — ephemeral, survives creature stop only.

The registry owns no model semantics; it is pure wiring around the managers.
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
    """Turn the constructor ``drive_store`` into a per-graph repo provider.

    - ``None`` → no provider (fall through to session/memory).
    - a callable → a graph-scoped factory ``store(gid) -> repo | None``.
    - a repository instance → a single-graph store claimed by the FIRST graph
      that asks for it; later graphs get ``None`` so one mutable repository is
      never blindly shared across disconnected graphs (design §7.1).
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
    """Owns the per-graph repositories + managers for a Drive-enabled engine."""

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
        # Each graph's explicit provider repository is resolved exactly ONCE and
        # cached, so a presence check never consumes a single-store provider and
        # a later resolve returns the same repo (design §7.1, R1-10). ``None`` is
        # cached too — a graph the single-store provider declines stays declined.
        self._provider_repos: dict[str, Any] = {}
        self._managers: dict[str, DriveManager] = {}
        self._repos: dict[str, Any] = {}
        # graph_id -> the SessionStore its session-backed repo was opened from,
        # so a repeated bind for the same store is a no-op (no leaked conn).
        self._bound_stores: dict[str, Any] = {}
        # Graphs whose dispatcher is allowed to run: a creature crossed the
        # restoration barrier (design §6.5 — no Drive delivered/claimed before
        # the barrier). Manager creation is eager (so tool ops resolve a repo);
        # the dispatcher START is gated on this set so it never claims/writes on
        # the shared session file DURING resume/state-injection.
        self._ready_graphs: set[str] = set()
        self._active = False
        self._start_tasks: set[asyncio.Task] = set()
        # Fire-and-forget stops of DETACHED managers (rebind / drop_graph). Kept
        # apart from _start_tasks so shutdown DRAINS them (awaits completion)
        # rather than cancelling a dispatcher stop mid-round.
        self._stop_tasks: set[asyncio.Task] = set()

    # -- read surface --------------------------------------------------------

    def peek(self, graph_id: str) -> DriveManager | None:
        """The manager for ``graph_id`` if one exists — never creates one."""
        return self._managers.get(graph_id)

    def all_managers(self) -> list[DriveManager]:
        """Every live per-graph manager (for cross-graph list unions, §3.1)."""
        return list(self._managers.values())

    def repository_for(self, graph_id: str) -> Any:
        return self._repos.get(graph_id)

    def durability_for(self, graph_id: str) -> str:
        """This graph's Drive durability, ``ephemeral`` if it has no repo yet."""
        repo = self._repos.get(graph_id)
        return repo.durability if repo is not None else "ephemeral"

    @property
    def durability(self) -> str:
        """Aggregate durability across graphs (design §7.1, R1-41).

        A single mode when every graph agrees, ``mixed`` when the engine holds
        both persistent and ephemeral graphs, and ``ephemeral`` when empty. A
        per-graph consumer must call :meth:`durability_for`."""
        modes = {repo.durability for repo in self._repos.values()}
        if not modes:
            return "ephemeral"
        if len(modes) == 1:
            return next(iter(modes))
        return "mixed"

    # -- creation / lifecycle ------------------------------------------------

    def manager_for(self, graph_id: str, *, create: bool = True) -> DriveManager | None:
        """Get-or-create the manager for ``graph_id`` (design §3.1).

        The tool + operation surface calls this: a creature creating its first
        Drive mints the graph's manager on demand, resolving the repository from
        the best source available right now (provider → attached store → memory).
        ``create=False`` peeks without minting."""
        existing = self._managers.get(graph_id)
        if existing is not None:
            return existing
        if not create:
            return None
        repo, store = self._resolve_repo(graph_id)
        return self._install(graph_id, repo, store)

    async def ensure_started(self, graph_id: str) -> DriveManager:
        """Mark the graph delivery-ready and start its dispatcher (design §6.5).

        This is the barrier-crossed signal: called from ``_reconcile_when_ready``
        once a creature is restoration-ready, and from topology merge/split (which
        operate on already-running graphs). Idempotent."""
        self._ready_graphs.add(graph_id)
        manager = self.manager_for(graph_id)
        assert manager is not None
        await manager.start()
        return manager

    async def bind_store(self, graph_id: str, session_store: Any) -> DriveManager:
        """Bind (or rebind) the graph's repository to a session-backed one.

        Called when a :class:`SessionStore` is attached/minted for the graph
        (autosession / resume), BEFORE the graph's creatures reach the
        restoration barrier (design §6.5, §7.1). If a manager was already minted
        with an ephemeral repository (an early tool-created Drive before the
        store attached), its rows migrate into the session-backed repository so
        nothing is lost. Idempotent for a store already bound to the graph."""
        if self._bound_stores.get(graph_id) is session_store:
            return self._managers[graph_id]
        # An explicit provider owns the repository selection; a session store
        # attach must not override it (design §7.1 precedence). Use the CACHED
        # provider resolution so the presence check does not consume a
        # single-store provider (R1-10).
        if self._provider_repo(graph_id) is not None:
            return self.manager_for(graph_id)
        repo = open_session_drive_repository(session_store)
        existing = self._managers.get(graph_id)
        if existing is None:
            # NO eager start: bind_store runs during resume/autosession attach,
            # BEFORE the restoration barrier. The dispatcher starts only when a
            # creature crosses the barrier (``ensure_started`` via reconcile), so
            # it never contends with resume's session-file writes (design §6.5).
            return self._install(graph_id, repo, session_store)
        await self._rebind(graph_id, existing, repo, session_store)
        return self._managers[graph_id]

    def rebind_repository(self, graph_id: str, repo: Any, store: Any = None) -> None:
        """Swap a graph's repository, keeping the graph (topology merge/split).

        The caller has already moved rows into ``repo`` AND closed the old repo
        (``_close_if_persistent``); here we only rewire the manager. The old
        dispatcher's stop is fire-and-forget: the graph's session store is NOT
        closing (the survivor/child keeps it), so there is no companion-closer
        race, and the old dispatcher quiesces harmlessly against the already-
        closed old repo (delivery's TestRepoCloseRace). Awaiting the stop here
        would instead block the topology op on the old dispatcher's teardown. A
        no-op when the repo is unchanged. Contrast :meth:`detach_and_stop`, which
        DOES await — used when the store itself is about to close."""
        existing = self._managers.get(graph_id)
        if existing is not None and self._repos.get(graph_id) is repo:
            return
        if existing is not None:
            self._detach(graph_id, existing)
        self._install(graph_id, repo, store)

    def drop_graph(self, graph_id: str) -> None:
        """Forget a graph whose topology went away entirely (a creature removed
        its last member). Fire-and-forget stop: this is a sync engine path, and
        the dispatcher tolerates the graph store's later close. A topology
        merge/split that DROPS a graph uses the awaited :meth:`detach_and_stop`
        instead, because it closes the store right after."""
        manager = self._managers.pop(graph_id, None)
        self._repos.pop(graph_id, None)
        self._bound_stores.pop(graph_id, None)
        self._ready_graphs.discard(graph_id)
        if manager is not None:
            self._schedule_stop(manager)

    async def detach_and_stop(self, graph_id: str) -> None:
        """AWAIT-stop + forget a graph's manager while its repo is still open.

        Called before a graph's session store (and its companion Drive repo) is
        closed — on store replacement, or when a topology merge/split drops the
        graph — so the dispatcher releases its claims against a live connection
        and is fully stopped BEFORE the store's companion closer shuts the repo
        executor (no fire-and-forget stop racing the close, design §6.4/§7.1)."""
        manager = self._managers.pop(graph_id, None)
        self._repos.pop(graph_id, None)
        self._bound_stores.pop(graph_id, None)
        self._ready_graphs.discard(graph_id)
        if manager is not None:
            try:
                await manager.stop()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("drive manager detach stop failed", error=str(exc))

    async def start_all(self) -> None:
        """Mark the registry active and start every barrier-ready dispatcher.

        Only graphs already marked delivery-ready start here; a graph mid-resume
        (bound but pre-barrier) waits for ``ensure_started`` at the barrier."""
        self._active = True
        for graph_id, manager in list(self._managers.items()):
            if graph_id in self._ready_graphs:
                await manager.start()

    async def stop_all(self) -> None:
        """Stop every dispatcher (engine shutdown / drain, design §6.4).

        Cancel pending START tasks (nothing should start mid-shutdown) but DRAIN
        in-flight scheduled STOPS (detached managers): awaiting them guarantees a
        dispatcher's ``_run`` is down before the loop is torn out from under it,
        rather than cancelling the stop and leaving a round to race a closing
        repo (the DriveRepositoryClosedError churn drive-gaps quiesced)."""
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
        """Publish a new enabled-registry snapshot to every graph's manager."""
        for manager in self._managers.values():
            manager.set_snapshot(snapshot)

    # -- internals -----------------------------------------------------------

    def _provider_repo(self, graph_id: str) -> Any:
        """The graph's explicit provider repository, resolved once and cached.

        Caches ``None`` too, so a single-store provider is claimed by exactly one
        graph and re-consulting it never re-claims or re-consumes it (R1-10)."""
        if graph_id in self._provider_repos:
            return self._provider_repos[graph_id]
        repo = self._provider(graph_id) if self._provider is not None else None
        self._provider_repos[graph_id] = repo
        return repo

    def _resolve_repo(self, graph_id: str) -> tuple[Any, Any]:
        """Resolve ``(repo, session_store)`` by the §7.1 precedence."""
        provided = self._provider_repo(graph_id)
        if provided is not None:
            return provided, None
        stores = getattr(self._engine, "_session_stores", {})
        store = stores.get(graph_id)
        if store is not None:
            return open_session_drive_repository(store), store
        return MemoryDriveRepository(), None

    def resolve_destination_repo(self, graph_id: str) -> tuple[Any, Any]:
        """The destination repository + store for a graph on topology movement.

        The ONE resolver shared by initial bind, merge, and split (design §7.1,
        R1-10): the explicit provider (cached) wins over the session store, which
        wins over an ephemeral memory repo — so caller-selected durability /
        isolation is preserved across merge and split, not silently replaced by
        the session sidecar. A provider repo is owned by the provider and must
        not be closed by the mover; a session/memory destination is the mover's."""
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
        """A trusted live-topology check for ``graph_id`` (R1-06): an assignment
        is valid only when its assignee is a current member of this graph and its
        canonical graph is this graph. When no live topology is available (a
        headless test engine, or the graph not yet in topology mid-setup) it
        returns True so reconcile never orphans on missing information."""
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
        """Migrate rows from an EPHEMERAL repo into the session repo, then swap.

        Only the memory→session promotion path migrates (a Drive created before
        the store attached). A session→session rebind never exports the old repo
        — the session repo already carries any persisted rows on resume, and a
        superseded store's sqlite connection may already be closed (topology
        merge/split move rows through their own path)."""
        old_repo = self._repos.get(graph_id)
        if getattr(old_repo, "durability", "") == "ephemeral":
            try:
                payload = await old_repo.export_rows()
                if payload.get("drives"):
                    await repo.import_rows(payload)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("drive repo rebind migrate failed", error=str(exc))
        await existing.stop()
        self._install(graph_id, repo, store)

    def _maybe_start(self, graph_id: str, manager: DriveManager) -> None:
        """Start a freshly-minted dispatcher when the registry is active, its
        graph is barrier-ready, and a loop is running (lazy tool-path creation).

        Gated on ``_ready_graphs`` so a manager minted DURING resume/bind (before
        the barrier) does not start its dispatcher and contend on the session file
        (design §6.5); the barrier's ``ensure_started`` starts it."""
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
        """Fire-and-forget stop for a manager whose repo is ALREADY closed (a
        rebind swap). Not for the store-close path — that awaits via
        :meth:`detach_and_stop` so the stop finishes before the store closes."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        task = asyncio.ensure_future(manager.stop())
        self._stop_tasks.add(task)
        task.add_done_callback(self._stop_tasks.discard)


__all__ = ["GraphDriveRegistry"]
