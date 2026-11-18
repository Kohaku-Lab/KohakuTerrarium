"""Ranked FTS/vector retrieval and source-aware search results.

The search layer is independent of SessionMemory index mutations.
"""

import time
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.session.memory.visibility import (
    AgentBranchVisibility,
    BranchVisibility,
    ranked_filtered,
)

# Format 1 indexes the selected lineage; format 2 staged all branches;
# format 3 added legacy ancestry, format 4 a source fingerprint, and
# format 5 records exact physical source refs and disambiguates mirrored IDs.
_SELECTED_INDEX_FORMAT = 1
_ALL_BRANCH_INDEX_FORMAT = 5


@dataclass
class SearchResult:
    """A single search result with metadata."""

    content: str
    round_num: int
    block_num: int
    agent: str
    block_type: str
    score: float
    ts: float = 0.0
    tool_name: str = ""
    channel: str = ""
    block_id: str = ""
    event_id: int | None = None
    event_position: int | None = None
    part_index: int = 0
    turn_index: int | None = None
    branch_id: int | None = None
    parent_branch_path: tuple[tuple[int, int], ...] | None = None
    source_fingerprint: str = ""
    source_ref: str = ""
    anchor: str = ""

    @property
    def age_str(self) -> str:
        """Return a compact elapsed-time label for the result timestamp."""
        if self.ts <= 0:
            return ""
        elapsed = time.time() - self.ts
        if elapsed < 60:
            return f"{int(elapsed)}s ago"
        if elapsed < 3600:
            return f"{int(elapsed / 60)}m ago"
        return f"{elapsed / 3600:.1f}h ago"


class MemorySearchMixin:
    """Read the ranked indexes owned by SessionMemory."""

    def search(
        self,
        query: str,
        mode: str = "auto",
        k: int = 10,
        agent: str | None = None,
        *,
        branch_view: dict[int, int] | None = None,
        branch_views: dict[str, dict[int, int]] | None = None,
    ) -> list[SearchResult]:
        """Search a ranked corpus, optionally constrained to branch views.

        A supplied view is the fully resolved turn-to-branch projection.
        None is an explicit low-level unscoped query (for maintenance).
        Selected-lineage indexes cannot answer arbitrary branch projections.
        """
        if branch_view is not None and branch_views is not None:
            raise ValueError("branch_view and branch_views are mutually exclusive")
        if (branch_view is not None or branch_views is not None) and (
            self._index_format != _ALL_BRANCH_INDEX_FORMAT
        ):
            raise ValueError("Branch-scoped search requires an all-branch memory index")
        # Index format is a property of the entire session. Legacy per-agent
        # format markers are migration inputs, not query authorization.
        if branch_view is not None or branch_views is not None:
            manifest = self._manifest()
            if manifest is not None and manifest.get("format") != self._index_format:
                raise ValueError(
                    "Memory index invalid or building; rebuild before search"
                )
        scope = (
            AgentBranchVisibility(branch_views)
            if branch_views is not None
            else BranchVisibility(branch_view) if branch_view is not None else None
        )
        if mode == "auto":
            mode = "hybrid" if self._has_vectors else "fts"

        match mode:
            case "fts":
                return self._search_fts(query, k, agent, scope=scope)
            case "semantic":
                if not self._has_vectors:
                    raise ValueError(
                        "semantic search needs an embedding model — "
                        "pass embedder= (see session.embedding."
                        "create_embedder) or run kt embedding first; "
                        "use mode='auto'/'hybrid' for graceful fallback"
                    )
                return self._search_semantic(query, k, agent, scope=scope)
            case "hybrid":
                if not self._has_vectors:
                    return self._search_fts(query, k, agent, scope=scope)
                return self._search_hybrid(query, k, agent, scope=scope)
            case _:
                raise ValueError(
                    f"Unknown search mode {mode!r} — expected 'fts', "
                    "'semantic', 'hybrid', or 'auto'"
                )

    def _search_fts(
        self,
        query: str,
        k: int,
        agent: str | None,
        *,
        scope: BranchVisibility | AgentBranchVisibility | None = None,
    ) -> list[SearchResult]:
        """FTS results ranked before branch and agent filtering."""
        revision = self._assert_fts_readable()
        if scope is not None:
            results = ranked_filtered(
                lambda limit: self._fts.search(query, k=limit),
                k=k,
                matches=lambda hit: (
                    (not agent or hit[2].get("agent") == agent)
                    and scope.matches(hit[2])
                ),
            )
        else:
            results = self._fts.search(query, k=k * 2)
        out = []
        for row_id, score, meta in results:
            if agent and meta.get("agent") != agent:
                continue
            text, _ = self._fts.get_by_id(row_id)
            out.append(_result_from_metadata(text or "", score, meta))
            if len(out) >= k:
                break
        if self._manifest() != revision:
            raise RuntimeError("Memory index changed during search; retry")
        return out

    def _search_semantic(
        self,
        query: str,
        k: int,
        agent: str | None,
        *,
        scope: BranchVisibility | AgentBranchVisibility | None = None,
    ) -> list[SearchResult]:
        """Vector semantic search."""
        if self._vec is None:
            return []
        query_vec = self._embedder.encode_one(query)
        return self._search_vector(query_vec, k, agent, scope=scope)

    def _search_vector(
        self,
        query_vec,
        k: int,
        agent: str | None,
        *,
        scope: BranchVisibility | AgentBranchVisibility | None = None,
    ) -> list[SearchResult]:
        # Never read ranked vectors while the session-wide manifest is
        # invalid/building, even if legacy agent counters are missing.
        self._assert_fts_readable()
        revision = self._assert_vector_manifest_readable()
        # The current model, corpus generation, and keyword watermark must
        # all agree before reading *any* ranked vector candidates. Checking
        # after Top-K could silently hide valid hits behind stale vectors.
        if agent:
            agents = (agent,)
        elif isinstance(scope, AgentBranchVisibility):
            agents = scope._views.keys()
        else:
            # The manifest, not legacy per-Agent counters, names the published
            # vector corpus. Missing counters must not hide stale physical rows.
            agents = revision.get("agents", ())
        for name in agents:
            self._assert_vector_readable(name)
        if scope is not None:
            results = ranked_filtered(
                lambda limit: self._vec.search(query_vec, k=limit),
                k=k,
                matches=lambda hit: (
                    (not agent or hit[2].get("agent") == agent)
                    and scope.matches(hit[2])
                ),
            )
        else:
            results = self._vec.search(query_vec, k=k * 2)
        out = []
        for _, distance, meta in results:
            if agent and meta.get("agent") != agent:
                continue
            out.append(
                _result_from_metadata(meta.get("content", ""), 1.0 - distance, meta)
            )
            if len(out) >= k:
                break
        if self._manifest() != revision:
            raise RuntimeError("Memory index changed during search; retry")
        return out

    def _search_hybrid(
        self,
        query: str,
        k: int,
        agent: str | None,
        *,
        scope: BranchVisibility | AgentBranchVisibility | None = None,
    ) -> list[SearchResult]:
        """Hybrid search: lineage-filtered candidates then RRF fusion."""
        fts_results = self._search_fts(query, k=k * 2, agent=agent, scope=scope)
        sem_results = self._search_semantic(query, k=k * 2, agent=agent, scope=scope)
        return self._fuse_results(fts_results, sem_results, k)

    @staticmethod
    def _fuse_results(fts_results, sem_results, k: int) -> list[SearchResult]:
        scores: dict[str, float] = {}
        result_map: dict[str, SearchResult] = {}

        for rank, r in enumerate(fts_results):
            key = r.block_id or f"{r.agent}:r{r.round_num}:b{r.block_num}"
            scores[key] = scores.get(key, 0) + 1.0 / (60 + rank)
            result_map[key] = r

        for rank, r in enumerate(sem_results):
            key = r.block_id or f"{r.agent}:r{r.round_num}:b{r.block_num}"
            scores[key] = scores.get(key, 0) + 1.0 / (60 + rank)
            if key not in result_map:
                result_map[key] = r

        ranked = sorted(scores.items(), key=lambda x: -x[1])[:k]
        out = []
        for key, score in ranked:
            r = result_map[key]
            r.score = score
            out.append(r)
        return out


def _source_result_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    path = meta.get("parent_branch_path")
    return {
        "event_id": meta.get("event_id"),
        "event_position": meta.get("event_position"),
        "part_index": meta.get("part_index", 0),
        "turn_index": meta.get("turn_index"),
        "branch_id": meta.get("branch_id"),
        "parent_branch_path": (
            tuple(tuple(pair) for pair in path) if path is not None else None
        ),
        "source_fingerprint": meta.get("source_fingerprint", ""),
        "source_ref": meta.get("source_ref", ""),
    }


def _result_from_metadata(
    content: str, score: float, meta: dict[str, Any]
) -> SearchResult:
    """Build a result with identical source metadata for FTS and vectors."""
    return SearchResult(
        content=content,
        round_num=meta.get("round", 0),
        block_num=meta.get("block", 0),
        agent=meta.get("agent", ""),
        block_type=meta.get("type", ""),
        score=score,
        ts=meta.get("ts", 0),
        tool_name=meta.get("tool_name", ""),
        channel=meta.get("channel", ""),
        block_id=meta.get("block_id", ""),
        **_source_result_metadata(meta),
    )
