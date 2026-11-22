"""Coordinate one session's source history with its materialized memory index.

Capture each Agent's source events once per reconciliation. Only this layer
chooses the participants; the index planner and FTS/vector materializers work
with those captured events and never discover other Agents themselves.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.session.store.events.discovery import discover_agents
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

from kohakuterrarium.session.memory.branch_catalog import (
    append_candidates,
    branch_candidates_valid,
    project_candidates,
)
from kohakuterrarium.session.memory.visibility import (
    requested_store_view,
    resolve_search_view,
)
from kohakuterrarium.session.memory.blocks import _anchor
from kohakuterrarium.session.memory.source import (
    SourceFrontier,
    TailHistory,
    _merge_entries,
    catalog_for_full,
    read_appended,
    source_frontier,
)


@dataclass(frozen=True)
class IndexSourceSnapshot:
    """Detached per-Agent history, shared by the FTS and vector passes."""

    events: dict[str, list[dict] | TailHistory]
    roster: tuple[str, ...] = ()
    frontier: SourceFrontier | None = None
    catalogs: dict[str, dict] = field(default_factory=dict)

    @property
    def agents(self) -> tuple[str, ...]:
        return self.roster or tuple(self.events)

    @property
    def unchanged_prefix_agents(self) -> frozenset[str]:
        """Agents whose READY keyword source was certified without new rows.

        Full captures are deliberately excluded: they can be recovering an
        incomplete index even when their event lists have not grown.
        """
        return frozenset(
            name
            for name, events in self.events.items()
            if name in self.catalogs
            and isinstance(events, TailHistory)
            and not events.suffix
        )

    @property
    def matches_published_fts_prefix(self) -> bool:
        """A proved, unchanged FTS source prefix, not Vector readiness.

        This relationship is derived from the certified per-Agent suffixes.
        It cannot disagree with their changed/unchanged classification.
        """
        return (
            self.frontier is not None
            and bool(self.agents)
            and len(self.unchanged_prefix_agents) == len(self.agents)
        )


class SessionIndexReconciler:
    """The single source-roster and cross-index refresh policy for a Session."""

    def __init__(self, store: Any, memory: Any) -> None:
        self._store = store
        self._memory = memory

    def capture(
        self, requested: list[str] | tuple[str, ...], *, all_agents: bool = False
    ) -> IndexSourceSnapshot:
        names = list(requested)
        frontier = source_frontier(self._store)
        # Full capture is the authorized place for physical-key discovery.
        # The same key snapshot establishes the roster and certifies that
        # *every* indexed Agent row was decoded (not only the final row).
        keys = None
        if all_agents:
            manifest = self._memory._manifest()
            if hasattr(self._store, "_event_key_names"):
                keys = self._store._event_key_names()
                declared = self._store.meta.get("agents") or []
                discovered = discover_agents(keys)
                known = [*declared, *discovered]
            else:
                known = self._store.load_meta().get("agents", [])
            names = [
                *known,
                *sorted(self._memory._known_agents()),
                *((manifest or {}).get("agents", ())),
                *names,
            ]
        names = list(dict.fromkeys(names))
        events = {name: self._store.get_memory_events(name) for name in names}
        if frontier != source_frontier(self._store):
            frontier = None  # Writer crossed the capture boundary; never seal.
        if frontier is not None and keys is not None:
            per_agent = Counter()
            for key in keys:
                namespace, separator, suffix = key.rpartition(":e")
                if separator and suffix.isascii() and suffix.isdecimal():
                    per_agent[namespace] += 1
            if len(keys) != frontier.physical_count or any(
                len(events[name]) != per_agent[name] for name in names
            ):
                # Skipped/unreadable physical records cannot certify a complete
                # prefix. Framework/attached namespaces need not be indexed,
                # but their records remain covered by the global frontier.
                frontier = None
        else:
            # Partial captures and Store adapters without a full physical-key
            # snapshot cannot publish a Session-wide source certificate.
            frontier = None
        catalogs = (
            {
                name: catalog
                for name, rows in events.items()
                if (catalog := catalog_for_full(rows, frontier)) is not None
            }
            if frontier is not None
            else {}
        )
        # Do not seal a partially-decoded or noncanonical source catalogue.
        if len(catalogs) != len(names):
            catalogs = {}
        return IndexSourceSnapshot(events, tuple(names), frontier, catalogs)

    def _catalog_state(self):
        # Catalogs are a derived fast-path only. Minimal/legacy index adapters
        # lacking this optional storage API retain the safe full capture path.
        return getattr(self._memory, "_state", None)

    def _published_catalogs(
        self, names: list[str], manifest: dict
    ) -> IndexSourceSnapshot | None:
        state = self._catalog_state()
        if state is None:
            return None
        frontier = source_frontier(self._store)
        if frontier is None:
            return None
        views: dict[str, list[dict] | TailHistory] = {}
        catalogs: dict[str, dict] = {}
        before_count = None
        total_suffix = 0
        for name in names:
            catalog = state.get(f"{name}:memory_source_catalog")
            keyword_key = f"{name}:cursor:keywords"
            keyword_revision = state.get(f"{keyword_key}:revision")
            if (
                not isinstance(catalog, dict)
                or keyword_key not in state
                or type(keyword_revision) is not int
                or catalog.get("keyword_revision") != keyword_revision
                or catalog.get("index_epoch") != manifest.get("epoch")
                or catalog.get("fts_revision") != manifest.get("fts_revision")
                or catalog.get("generation") != self._memory._generation(name)
                or state.get(f"{keyword_key}:pending", False)
                # A separate derived branch value must exist; checking only
                # its key avoids deserializing historical ancestry in a
                # source-proof read. Its source binding is checked at use.
                or f"{name}:memory_branch_catalog" not in state
            ):
                return None
            saved_frontier = catalog.get("frontier")
            if (
                not isinstance(saved_frontier, list)
                or len(saved_frontier) != 2
                or type(saved_frontier[1]) is not int
                or (before_count is not None and before_count != saved_frontier[1])
            ):
                return None
            before_count = saved_frontier[1]
            result = read_appended(self._store, name, catalog, frontier)
            if result is None:
                return None
            view, latest = result
            if view.suffix:
                # The entire old branch materialization is accessed only for
                # an actual Agent suffix, not for unchanged source proofs or
                # peer-Agent appends.
                branches = state.get(f"{name}:memory_branch_catalog")
                if not branch_candidates_valid(
                    branches, count=catalog["count"], anchor=catalog["anchor"]
                ):
                    return None
                updated_branches = append_candidates(
                    branches, _merge_entries([], view.suffix)
                )
                updated_branches["count"] = latest["count"]
                updated_branches["anchor"] = latest["anchor"]
                latest["_branches"] = updated_branches
            total_suffix += len(view.suffix)
            views[name] = view
            catalogs[name] = latest
        if before_count is None or (
            total_suffix != frontier.physical_count - before_count
        ):
            # Known per-Agent physical suffixes must account for every new
            # source row. Otherwise a hot-plugged/unknown namespace or a
            # broken sequence was not captured: discover the whole roster.
            return None
        # The source can advance while multiple Agent catalogues are being
        # consulted. A single initial frontier does not certify their shared
        # snapshot; fail closed to one full capture on a concurrent append.
        if source_frontier(self._store) != frontier:
            return None
        return IndexSourceSnapshot(views, tuple(names), frontier, catalogs)

    def _publish_source_catalogs(self, source: IndexSourceSnapshot) -> None:
        if (
            source.matches_published_fts_prefix
            or not source.catalogs
            or source.frontier is None
        ):
            return
        state = self._catalog_state()
        if state is None:
            return
        # Only KT-controlled append-only source mutations participate in this
        # proof. Do not persist a frontier after a concurrent append.
        if source_frontier(self._store) != source.frontier:
            return
        manifest = self._memory._manifest()
        if not manifest or manifest.get("fts") != "ready":
            return
        pending = []
        unchanged = source.unchanged_prefix_agents
        for name, catalog in source.catalogs.items():
            keyword_key = f"{name}:cursor:keywords"
            revision = state.get(f"{keyword_key}:revision")
            generation = self._memory._generation(name)
            is_pending = state.get(f"{keyword_key}:pending", False)
            # The prior Source Certificate already tied an unchanged Agent's
            # full checkpoint to its revision, epoch and generation. Bob's
            # append must not deserialize Alice's historical Round cursor.
            unchanged_certified = (
                name in unchanged
                and type(revision) is int
                and catalog.get("keyword_revision") == revision
                and catalog.get("index_epoch") == manifest["epoch"]
                and catalog.get("generation") == generation
                and not is_pending
            )
            if not unchanged_certified:
                checkpoint = state.get(keyword_key)
                if (
                    not isinstance(checkpoint, dict)
                    or checkpoint.get("count") != catalog.get("count")
                    or checkpoint.get("anchor") != catalog.get("anchor")
                    or checkpoint.get("index_epoch") != manifest["epoch"]
                    or checkpoint.get("generation") != generation
                    or not checkpoint.get("source_refs_complete", False)
                    or is_pending
                ):
                    continue
            key = f"{name}:memory_source_catalog"
            # Source proof is a small, independently readable derived value.
            # A candidate materialization belongs to that same accepted
            # source prefix; store both in one KVault batch for atomicity.
            branches = catalog.get("_branches")
            next_catalog = {
                **{
                    field: value
                    for field, value in catalog.items()
                    if field != "_branches"
                },
                "index_epoch": manifest["epoch"],
                "fts_revision": manifest["fts_revision"],
                # Every successful keyword mutation advances this small
                # revision. It proves that the large Round cursor is still
                # the one accepted at publication, without decoding it on
                # every stable query.
                "keyword_revision": revision,
                "generation": generation,
            }
            if state.get(key) != next_catalog:
                pending.append((key, next_catalog))
            if branches is not None:
                branches = {
                    **branches,
                    "count": catalog["count"],
                    "anchor": catalog["anchor"],
                }
                branch_key = f"{name}:memory_branch_catalog"
                # Full capture/changed Agent only: no heavy branch value
                # lookup occurs when Bob appended but Alice did not.
                pending.append((branch_key, branches))
        if pending:
            state.put_many(pending)

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
        unchanged = source.unchanged_prefix_agents
        for name, events in source.events.items():
            if name in unchanged:
                continue
            batch, blocks = self._memory._prepare(name, events, "keywords", 0)
            if batch.reset or batch.start != len(events) or blocks:
                return True
        return False

    def refresh_keywords(
        self, source: IndexSourceSnapshot, *, existing_only: bool = False
    ) -> dict[str, int]:
        if source.matches_published_fts_prefix:
            return {name: 0 for name in source.agents}
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
            unchanged = source.unchanged_prefix_agents
            active = tuple(name for name in source.agents if name not in unchanged)
            if self._needs_keyword_roster(source):
                self._memory._enroll_build_agents(active, with_vectors=False)
            updated = {
                name: (
                    0
                    if name in unchanged
                    else self._memory._index_keywords(name, events)
                )
                for name, events in source.events.items()
            }
            self._publish_source_catalogs(source)
            return updated
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
        views = {}
        for name in dict.fromkeys(requested):
            requested_view = (
                overrides[name]
                if overrides is not None and name in overrides
                else requested_store_view(self._store, name)
            )
            if name in source.catalogs:
                catalog = source.catalogs[name]
                branches = catalog.get("_branches")
                if branches is None:
                    branches = self._memory._state.get(f"{name}:memory_branch_catalog")
                if not branch_candidates_valid(
                    branches, count=catalog["count"], anchor=catalog["anchor"]
                ):
                    raise ValueError(
                        "Memory branch catalogue does not match source proof; "
                        "rebuild before branch selection"
                    )
                views[name] = (
                    dict(branches["default_view"])
                    if requested_view is None
                    else project_candidates(branches, requested_view)
                )
            else:
                views[name] = resolve_search_view(source.events[name], requested_view)
        return views

    def probe_source_prefix(
        self, requested: list[str] | tuple[str, ...]
    ) -> IndexSourceSnapshot | None:
        """Prove a published, complete Agent source prefix without discovery.

        Used by both searches and read-only status. It does not materialize,
        mutate, or re-enumerate the event keyspace; failure means the caller
        must choose its own full discovery/reconciliation behavior.
        """
        manifest = self._memory._manifest()
        if (
            manifest is None
            or manifest.get("format") != self._memory._index_format
            or manifest.get("fts") != "ready"
        ):
            return None
        names = list(
            dict.fromkeys(
                [
                    *(self._store.meta.get("agents") or []),
                    *manifest.get("agents", ()),
                    *requested,
                ]
            )
        )
        return self._published_catalogs(names, manifest)

    def capture_or_reuse(
        self, requested: list[str] | tuple[str, ...], *, all_agents: bool = False
    ) -> IndexSourceSnapshot:
        """One source authority for both saved and live search."""
        reused = self.probe_source_prefix(requested)
        if reused is not None:
            return reused
        manifest = self._memory._manifest()
        migration = (
            manifest is None
            or manifest.get("format") != self._memory._index_format
            or manifest.get("fts") != "ready"
        )
        return self.capture(
            requested,
            all_agents=all_agents
            or migration
            or bool(manifest and manifest.get("agents")),
        )

    def prepare_keywords(
        self,
        requested: list[str],
        overrides: dict[str, dict[int, int]] | None = None,
        *,
        existing_only: bool = False,
        all_agents: bool = False,
        project_all: bool = False,
    ) -> tuple[IndexSourceSnapshot, dict[str, dict[int, int]]]:
        source = self.capture_or_reuse(requested, all_agents=all_agents)
        self.refresh_keywords(source, existing_only=existing_only)
        return source, self.selected_views(
            source, source.agents if project_all else requested, overrides
        )

    def _can_append_vectors(self, source: IndexSourceSnapshot, manifest: dict) -> bool:
        """Incrementally encode only a proven appended suffix.

        A READY/DIRTY vector corpus with unchanged model/generation can reuse the
        prior Agent checkpoints. Missing, interrupted or rewritten prefixes
        require full source capture; a virtual TailHistory cannot replay them.
        """
        if (
            manifest.get("vector") not in ("ready", "dirty")
            or manifest.get("vector_identity") != self._memory._vector_identity
            or manifest.get("vector_dimensions") != self._memory._embedder.dimensions
            or manifest.get("vector_needs_clear", False)
            or type(manifest.get("vector_generation")) is not int
            or source.frontier is None
            or not set(manifest.get("agents", ())) <= set(source.agents)
        ):
            return False
        dims = self._memory._embedder.dimensions
        state = self._memory._state
        for name in source.agents:
            events = source.events[name]
            if not isinstance(events, TailHistory):
                return False
            key = f"{name}:cursor:vectors:{dims}"
            checkpoint = state.get(key)
            if events.old_count == 0 and not checkpoint:
                continue
            if (
                not isinstance(checkpoint, dict)
                or checkpoint.get("count") != events.old_count
                or checkpoint.get("index_epoch") != manifest.get("epoch")
                or checkpoint.get("vector_generation")
                != manifest.get("vector_generation")
                or checkpoint.get("embedder_identity") != self._memory._vector_identity
                or checkpoint.get("generation") != self._memory._generation(name)
                or not checkpoint.get("source_refs_complete", False)
                or not checkpoint.get("all_branches", False)
                or state.get(f"{key}:pending", False)
                or (
                    events.old_count > 0
                    and (
                        events.previous is None
                        or checkpoint.get("anchor") != _anchor(events.previous)
                    )
                )
                or (
                    events.old_count == 0 and checkpoint.get("anchor") not in ("", None)
                )
            ):
                return False
            # A same-length FTS rewrite (or an aborted keyword rebuild)
            # cannot be proven compatible from an append cursor alone.
            if not events.suffix and checkpoint.get("keyword_revision") != state.get(
                f"{name}:cursor:keywords:revision", 0
            ):
                return False
        return True

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
        if not rebuild and source.matches_published_fts_prefix:
            try:
                for name in requested:
                    self._memory._assert_vector_readable(name)
            except ValueError:
                rebuild = True
            else:
                return source, ()
        if rebuild:
            if manifest is not None and self._can_append_vectors(source, manifest):
                return source, source.agents
            source = self.capture(requested, all_agents=True)
            self.refresh_keywords(source)
            return source, source.agents
        return source, tuple(dict.fromkeys(requested))

    def _published_source_is_current(self, source: IndexSourceSnapshot) -> bool:
        """Pure no-op preflight, never a publication/ownership decision.

        A complete READY manifest and matching per-agent FTS/vector
        checkpoints are required. Any doubt takes the normal build path;
        this optimization cannot turn incomplete work into a published index.
        """
        manifest = self._memory._manifest()
        if (
            not manifest
            or manifest.get("format") != self._memory._index_format
            or manifest.get("fts") != "ready"
            or (
                self._memory._has_vectors
                and (
                    manifest.get("vector") != "ready"
                    or manifest.get("vector_identity") != self._memory._vector_identity
                    or manifest.get("vector_dimensions")
                    != self._memory._embedder.dimensions
                )
            )
            or not set(source.agents) <= set(manifest.get("agents", ()))
        ):
            return False
        for name, events in source.events.items():
            for kind in (
                ["keywords", f"vectors:{self._memory._embedder.dimensions}"]
                if self._memory._has_vectors
                else ["keywords"]
            ):
                plan, blocks = self._memory._prepare(name, events, kind, 0)
                if plan.reset or plan.start != len(events) or blocks:
                    return False
        return True

    def build(
        self,
        source: IndexSourceSnapshot,
        *,
        force: bool = False,
        progress=None,
    ) -> dict[str, int]:
        """Materialize every captured Agent, including empty namespaces."""
        try:
            if source.matches_published_fts_prefix:
                manifest = self._memory._manifest() or {}
                reuse_vectors = not self._memory._has_vectors or (
                    manifest.get("vector") == "ready"
                    and manifest.get("vector_identity") == self._memory._vector_identity
                    and manifest.get("vector_dimensions")
                    == self._memory._embedder.dimensions
                )
                if not force and reuse_vectors:
                    try:
                        if self._memory._has_vectors:
                            for name in source.agents:
                                self._memory._assert_vector_readable(name)
                    except ValueError:
                        reuse_vectors = False
                    else:
                        result = {name: 0 for name in source.agents}
                        if progress is not None:
                            for name in source.agents:
                                progress(name, source.catalogs[name]["count"], 0)
                        return result
                # FTS can be published while vector rebuilding is still
                # incomplete. Such a build needs the real source, not a
                # virtual empty suffix.
                if force or not reuse_vectors:
                    source = self.capture(list(source.agents), all_agents=True)
            if force and any(
                isinstance(events, TailHistory) for events in source.events.values()
            ):
                source = self.capture(list(source.agents), all_agents=True)
            if (
                not force
                and not source.matches_published_fts_prefix
                and any(
                    isinstance(events, TailHistory) for events in source.events.values()
                )
                and self._memory._has_vectors
                and not self._can_append_vectors(source, self._memory._manifest() or {})
            ):
                # Saved build() may skip prepare_vectors(), so its destructive
                # vector recovery must recapture the complete source.
                source = self.capture(list(source.agents), all_agents=True)
            if not force and self._published_source_is_current(source):
                # An old v2 index may be fully current but predate the
                # optional source catalogue. Seal it once without touching
                # the published Manifest or interrupting any reader.
                self._publish_source_catalogs(source)
                result = {name: 0 for name in source.agents}
                if progress is not None:
                    for name, events in source.events.items():
                        progress(name, len(events), 0)
                return result
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
            self._publish_source_catalogs(source)
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
