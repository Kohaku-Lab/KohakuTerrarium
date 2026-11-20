"""Coordinate one session's source history with its materialized memory index.

Capture each Agent's source events once per reconciliation. Only this layer
chooses the participants; the index planner and FTS/vector materializers work
with those captured events and never discover other Agents themselves.
"""

from dataclasses import dataclass
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

from kohakuterrarium.session.memory.visibility import (
    requested_store_view,
    resolve_search_view,
)


@dataclass(frozen=True)
class IndexSourceSnapshot:
    """Detached per-Agent history, shared by the FTS and vector passes."""

    events: dict[str, list[dict]]

    @property
    def agents(self) -> tuple[str, ...]:
        return tuple(self.events)


class SessionIndexReconciler:
    """The single source-roster and cross-index refresh policy for a Session."""

    def __init__(self, store: Any, memory: Any) -> None:
        self._store = store
        self._memory = memory

    def capture(
        self, requested: list[str] | tuple[str, ...], *, all_agents: bool = False
    ) -> IndexSourceSnapshot:
        names = list(requested)
        if all_agents:
            manifest = self._memory._manifest()
            names = [
                *self._store.load_meta().get("agents", []),
                *sorted(self._memory._known_agents()),
                *((manifest or {}).get("agents", ())),
                *names,
            ]
        names = list(dict.fromkeys(names))
        return IndexSourceSnapshot(
            {name: self._store.get_memory_events(name) for name in names}
        )

    def _needs_keyword_roster(self, source: IndexSourceSnapshot) -> bool:
        """Avoid rebuilding a READY roster when every captured source is current.

        All checks are pure planning; mutations and revalidation remain under
        Lifecycle's short lock. A single Agent already gets an atomic
        BUILDING barrier from its own _index_keywords operation.
        """
        if len(source.agents) < 2:
            return False
        manifest = self._memory._manifest()
        if (
            manifest is None
            or manifest.get("format") != self._memory._index_format
            or manifest.get("fts") != "ready"
        ):
            return True
        for name, events in source.events.items():
            batch, blocks = self._memory._prepare(name, events, "keywords", 0)
            if batch.reset or batch.start != len(events) or blocks:
                return True
        return False

    def refresh_keywords(
        self, source: IndexSourceSnapshot, *, existing_only: bool = False
    ) -> dict[str, int]:
        if (
            existing_only
            and self._memory._manifest() is None
            and not any(
                self._memory._get_indexed_count(name) > 0 for name in source.agents
            )
        ):
            # SessionReader preserves its historical explicit-index contract.
            return {}
        try:
            if self._needs_keyword_roster(source):
                self._memory._enroll_build_agents(source.agents, with_vectors=False)
            return {
                name: self._memory._index_keywords(name, events)
                for name, events in source.events.items()
            }
        except BaseException:
            # Like explicit build(), a cross-Agent keyword reconciliation
            # must abandon its incomplete Session-wide roster on failure.
            try:
                self._memory._abort_builds()
            except BaseException:
                logger.exception("Keyword reconciliation abort failed")
            raise

    def selected_views(
        self,
        source: IndexSourceSnapshot,
        requested: list[str] | tuple[str, ...],
        overrides: dict[str, dict[int, int]] | None = None,
    ) -> dict[str, dict[int, int]]:
        return {
            name: resolve_search_view(
                source.events[name],
                (
                    overrides[name]
                    if overrides is not None and name in overrides
                    else requested_store_view(self._store, name)
                ),
            )
            for name in dict.fromkeys(requested)
        }

    def prepare_keywords(
        self,
        requested: list[str],
        overrides: dict[str, dict[int, int]] | None = None,
        *,
        existing_only: bool = False,
        all_agents: bool = False,
        project_all: bool = False,
    ) -> tuple[IndexSourceSnapshot, dict[str, dict[int, int]]]:
        manifest = self._memory._manifest()
        migration = (
            manifest is None
            or manifest.get("format") != self._memory._index_format
            or manifest.get("fts") != "ready"
        )
        source = self.capture(requested, all_agents=all_agents or migration)
        self.refresh_keywords(source, existing_only=existing_only)
        return source, self.selected_views(
            source, source.agents if project_all else requested, overrides
        )

    def prepare_vectors(
        self, source: IndexSourceSnapshot, requested: list[str]
    ) -> tuple[IndexSourceSnapshot, tuple[str, ...]]:
        """Refresh FTS for the *same source events* used by a vector rebuild.

        On a full vector rebuild, capture all Agents regardless of the caller's
        search scope. Keeping selected views separate avoids leaking peer hits.
        """
        manifest = self._memory._manifest()
        rebuild = (
            manifest is None
            or manifest.get("vector") != "ready"
            or manifest.get("vector_identity") != self._memory._vector_identity
            or manifest.get("vector_dimensions") != self._memory._embedder.dimensions
        )
        if rebuild:
            source = self.capture(requested, all_agents=True)
            self.refresh_keywords(source)
            return source, source.agents
        return source, tuple(dict.fromkeys(requested))

    def build(
        self,
        source: IndexSourceSnapshot,
        *,
        force: bool = False,
        progress=None,
    ) -> dict[str, int]:
        """Materialize every captured Agent, including empty namespaces."""
        try:
            if force:
                self._memory.force_rebuild(list(source.agents))
            # Reserve the entire captured roster *before* Alice can publish
            # the Session-wide FTS/vector index while Bob is still pending.
            self._memory._enroll_build_agents(source.agents)
            results = {}
            for name, events in source.events.items():
                results[name] = self._memory.index_events(name, events)
                if progress is not None:
                    progress(name, len(events), results[name])
            return results
        except BaseException:
            # A callback may fail between Agents while the shared index is
            # legitimately BUILDING. End that logical build, not only the
            # individual materializer that happened to run last.
            try:
                self._memory._abort_builds()
            except BaseException:
                logger.exception("Session reconciliation abort failed")
            raise
