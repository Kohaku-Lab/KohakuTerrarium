"""Persistence namespace for the :class:`Studio` façade.

Provides the typed ``studio.persistence`` namespaces while delegating storage,
indexing, resume, fork, and viewer behavior to their owning persistence modules.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.studio.persistence import (
    fork as _persistence_fork,
    history as _persistence_history,
    projects as _projects,
    resume as _persistence_resume,
    store as _persistence_store,
)
from kohakuterrarium.studio.persistence.session_index import (
    aggregate_stats as _persistence_aggregate_stats,
    get_session_index_default as _persistence_get_index,
)
from kohakuterrarium.studio.persistence.session_index.reconcile import (
    reconcile as _persistence_reconcile,
)
from kohakuterrarium.studio.persistence.viewer import (
    diff as _viewer_diff,
    events as _viewer_events,
    exchanges as _viewer_exchanges,
    export as _viewer_export,
    summary as _viewer_summary,
    tree as _viewer_tree,
    turns as _viewer_turns,
)
from kohakuterrarium.studio.sessions import handles as _session_handles
from kohakuterrarium.studio.sessions import lifecycle as _session_lifecycle
from kohakuterrarium.studio.sessions.summary import edit as _summary_edit

if TYPE_CHECKING:
    from kohakuterrarium.studio.studio import Studio


class _PersistenceNS:
    """Saved-session list / resume / fork / viewer / memory."""

    def __init__(self, studio: "Studio") -> None:
        self._studio = studio
        self.viewer = _PersistenceViewer()
        self.projects = _PersistenceProjects(studio)

    def list(
        self,
        *,
        refresh: bool = False,
        full_rescan: bool = False,
        search: str = "",
        sort: str = "last_active",
        order: str = "desc",
        status: str | None = None,
        config_type: str | None = None,
        node_id: str | None = None,
        project: str | None = None,
    ) -> "list[dict]":
        """Return every indexed session matching the supplied filters.

        Listing reads only the sidecar index and deliberately does not paginate.
        ``refresh`` reconciles external changes incrementally, while
        ``full_rescan`` bypasses cached fingerprints and re-reads every session
        file. ``project`` keeps one project's sessions (``"none"``: sessions
        without a folder); every row carries ``project`` (``{"id", "name"}``).
        """
        session_dir = _persistence_store._session_dir()
        index = _persistence_get_index(session_dir)
        if refresh or full_rescan:
            _persistence_reconcile(index, session_dir, full=full_rescan)
        view, locations = _projects.session_scope(index, project)
        # The programmatic contract is unpaginated, so the shared index query is
        # widened to the current row count rather than inheriting HTTP limits.
        total = index.count() or 1
        page = index.list(
            search=search,
            status=status,
            config_type=config_type,
            node_id=node_id,
            sort=sort,
            order=order,
            limit=total,
            offset=0,
            locations=locations,
        )
        _projects.annotate_rows(view, page.rows)
        return page.rows

    def stats(
        self,
        *,
        refresh: bool = False,
        full_rescan: bool = False,
    ) -> dict[str, Any]:
        """Return session-index aggregations in the HTTP statistics shape.

        ``refresh`` and ``full_rescan`` use the same reconciliation semantics as
        :meth:`list`.
        """
        session_dir = _persistence_store._session_dir()
        index = _persistence_get_index(session_dir)
        if refresh or full_rescan:
            _persistence_reconcile(index, session_dir, full=full_rescan)
        return _persistence_aggregate_stats(index)

    async def resume(
        self,
        store_or_path: str | Path,
        *,
        pwd_override: str | None = None,
        workspace_overrides: dict[str, str] | None = None,
        llm: str | None = None,
    ) -> _session_handles.Session:
        return await _persistence_resume.resume_session(
            self._studio._service,
            store_or_path,
            pwd_override=pwd_override,
            workspace_overrides=workspace_overrides,
            llm=llm,
        )

    def announce_migration(self, path: Path) -> None:
        _persistence_resume.announce_migration_if_needed(path)

    async def fork(
        self,
        session_path: str | Path,
        *,
        at_event_id: int,
        mutate_kind: str | None = None,
        mutate_args: dict | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        return await _persistence_fork.fork_session_handler(
            Path(session_path),
            at_event_id=at_event_id,
            mutate_kind=mutate_kind,
            mutate_args=mutate_args,
            name=name,
        )

    def delete(self, name: str) -> "list[Path]":
        return _persistence_store.delete_session_files(name)

    def history_index(self, path: Path) -> dict[str, Any]:
        return _persistence_history.history_index_payload(path)

    def history(self, path: Path, target: str) -> dict[str, Any]:
        return _persistence_history.history_payload(path, target)

    def resolve_path(self, name: str) -> Path | None:
        return _persistence_store.resolve_session_path_default(name)

    async def set_summary(self, store: Any, text: str) -> dict[str, Any]:
        """Hand-write a session's one-line summary; empty text returns it to automatic."""
        record = await _summary_edit.set_user_summary(store, text)
        await _summary_edit.reindex(store)
        return record

    async def refresh_summary(self, store: Any) -> dict[str, Any]:
        """Regenerate a session's one-line summary from the configured source now."""
        record = await _summary_edit.regenerate(store)
        await _summary_edit.reindex(store)
        return record


class _PersistenceProjects:
    """Working-folder projects over the default session directory."""

    def __init__(self, studio: "Studio") -> None:
        self._studio = studio

    def _index(self) -> Any:
        return _persistence_get_index(_persistence_store._session_dir())

    def _running(self) -> "list[tuple[str, str]]":
        service = self._studio._service
        out: list[tuple[str, str]] = []
        for listing in _session_lifecycle.list_sessions(service):
            try:
                session = _session_lifecycle.get_session(service, listing.session_id)
            except KeyError:
                continue
            out.append((listing.node_id, session.pwd))
        return out

    def list(self, *, include_hidden: bool = False) -> dict[str, Any]:
        """``{"projects", "unassigned"}`` as ``GET /api/projects`` returns it."""
        return _projects.list_projects(
            self._index(), live=self._running(), include_hidden=include_hidden
        )

    def add(
        self, path: str, *, node: str | None = None, name: str | None = None
    ) -> dict[str, Any]:
        return _projects.add_project(
            self._index(), path, node=node, name=name, live=self._running()
        )

    def update(self, project_id: str, **changes: Any) -> dict[str, Any]:
        """Change ``name`` / ``pinned`` / ``hidden``; a found project is saved."""
        return _projects.update_project(
            self._index(), project_id, changes, live=self._running()
        )

    def forget(self, project_id: str) -> dict[str, str]:
        return _projects.forget_project(project_id)


class _PersistenceViewer:
    """Build read-only tree, summary, history, diff, and export payloads."""

    def tree(self, store: Any, session_name: str) -> dict[str, Any]:
        return _viewer_tree.build_tree_payload(store, session_name)

    def summary(
        self, store: Any, session_name: str, agent: str | None = None
    ) -> dict[str, Any]:
        return _viewer_summary.build_summary_payload(store, session_name, agent)

    def exchanges(
        self,
        store: Any,
        session_name: str,
        *,
        agent: str | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        return _viewer_exchanges.build_exchanges_payload(
            store, session_name, agent=agent, limit=limit
        )

    def turns(
        self,
        store: Any,
        session_name: str,
        *,
        agent: str | None = None,
        from_turn: int | None = None,
        to_turn: int | None = None,
        limit: int = 100,
        offset: int = 0,
        aggregate: bool = False,
    ) -> dict[str, Any]:
        return _viewer_turns.build_turns_payload(
            store,
            session_name,
            agent=agent,
            from_turn=from_turn,
            to_turn=to_turn,
            limit=limit,
            offset=offset,
            aggregate=aggregate,
        )

    def events(
        self,
        store: Any,
        session_name: str,
        *,
        agent: str | None = None,
        turn_index: int | None = None,
        types: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 200,
        cursor: int | None = None,
    ) -> dict[str, Any]:
        return _viewer_events.build_events_payload(
            store,
            session_name,
            agent=agent,
            turn_index=turn_index,
            types=types,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=limit,
            cursor=cursor,
        )

    def diff(
        self,
        a_path: Path,
        b_path: Path,
        *,
        agent: str | None = None,
    ) -> dict[str, Any]:
        return _viewer_diff.build_diff_payload(a_path, b_path, agent=agent)

    def export(
        self,
        store: Any,
        session_name: str,
        fmt: str = "md",
        agent: str | None = None,
    ) -> tuple[str, str]:
        return _viewer_export.build_export(store, session_name, fmt, agent)
