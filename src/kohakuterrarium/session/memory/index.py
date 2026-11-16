"""Index session history for keyword, semantic, and hybrid search.

Events are grouped into rounds and searchable text, tool, trigger, or user blocks.
"""

import time
from dataclasses import dataclass
from typing import Any

from kohakuvault import KVault, TextVault, VectorKVault

from kohakuterrarium.session.memory.embedding import BaseEmbedder, NullEmbedder
from kohakuterrarium.session.memory.resources import MemoryResources
from kohakuterrarium.session.memory.blocks import (
    Block,
    RebuildRequired,
    TOOL_RESULT_INDEX_CHARS as TOOL_RESULT_INDEX_CHARS,
    _content_to_text as _content_to_text,
    _extract_blocks as _extract_blocks,
    extract_batch,
)
from kohakuterrarium.session.store.lifecycle.version import require_writable_format
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Format 1 indexes the selected lineage; format 2 retains all branches.
# Format 2 is staged until filtered top-k retrieval is available.
_SELECTED_INDEX_FORMAT = 1
_ALL_BRANCH_INDEX_FORMAT = 2


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


@dataclass
class _IndexBatch:
    start: int
    revision: int
    generation: int
    embedder: object
    checkpoint: dict


class SessionMemory:
    """Search index over session event history.

    Manages FTS5 (keyword) and vector (semantic) indexes for a session.
    Indexes are stored in the same .kohakutr SQLite file as the session.
    """

    def __init__(
        self,
        db_path: str,
        embedder: BaseEmbedder | None = None,
        *,
        index_all_branches: bool = False,
    ):
        require_writable_format(db_path)
        self._index_format = (
            _ALL_BRANCH_INDEX_FORMAT if index_all_branches else _SELECTED_INDEX_FORMAT
        )
        self._path = db_path
        self._embedder = embedder or NullEmbedder()
        self._has_vectors = not isinstance(self._embedder, NullEmbedder)

        self._resources = MemoryResources(db_path)
        try:
            self._open_indexes(db_path)
        except BaseException as primary:
            try:
                self._resources.close()
            except BaseException as cleanup:
                tail = primary
                seen = {id(primary)}
                while tail.__cause__ is not None and id(tail.__cause__) not in seen:
                    tail = tail.__cause__
                    seen.add(id(tail))
                tail.__cause__ = cleanup
                raise primary
            raise

    def _open_indexes(self, db_path: str) -> None:
        self._fts = self._resources.open(TextVault, db_path, table="memory_fts")
        self._fts.enable_auto_pack()

        self._state = self._resources.open(KVault, db_path, table="memory_state")
        self._state.enable_auto_pack()

        # Dimension-specific tables prevent incompatible embedding models from mixing.
        self._vec: VectorKVault | None = None
        if self._has_vectors and self._embedder.dimensions > 0:
            dims = self._embedder.dimensions
            self._vec = self._resources.open(
                VectorKVault,
                db_path,
                table=f"memory_vec_{dims}d",
                dimensions=dims,
            )
            self._state["vec_dimensions"] = dims
        elif not self._has_vectors:
            # Existing vector tables remain queryable without a configured embedder.
            try:
                saved_dims = self._state["vec_dimensions"]
                if saved_dims and saved_dims > 0:
                    self._vec = self._resources.open(
                        VectorKVault,
                        db_path,
                        table=f"memory_vec_{saved_dims}d",
                        dimensions=saved_dims,
                    )
            except (KeyError, Exception):
                pass

    @property
    def has_vectors(self) -> bool:
        return self._vec is not None

    def close(self) -> None:
        """Release the native SQLite handles this index opened.

        These indexes own handles separate from ``SessionStore``.
        """
        self._resources.close()

    def _get_indexed_count(self, agent: str) -> int:
        """Get number of events already indexed for an agent."""
        return self._state.get(
            f"{agent}:indexed_keywords", self._state.get(f"{agent}:indexed_events", 0)
        )

    def _set_indexed_count(self, agent: str, count: int) -> None:
        self._state[f"{agent}:indexed_keywords"] = count
        if count == 0:
            self._state[f"{agent}:generation"] = self._generation(agent) + 1
            dims = self._embedder.dimensions or self._state.get("vec_dimensions", 0)
            self._state[f"{agent}:indexed_vectors:{dims}"] = 0
            self._state[f"{agent}:indexed_events"] = 0

    def _generation(self, agent: str) -> int:
        return self._state.get(f"{agent}:generation", 0)

    def _clear_fts(self, agent: str) -> None:
        self._clear_index(agent, "keywords")

    def _ensure_index_format(self, agent: str) -> None:
        """Invalidate FTS and vector indexes together if their corpus changes.

        The generation fence rejects in-flight vector batches prepared under
        the old projection, without modifying the persisted session event log.
        """
        key = f"{agent}:index_format"
        if self._state.get(key, _SELECTED_INDEX_FORMAT) != self._index_format:
            self._state[key] = self._index_format
            self._state[f"{agent}:generation"] = self._generation(agent) + 1

    def _clear_index(self, agent: str, kind: str) -> None:
        vault = self._fts if kind == "keywords" else self._vec
        if kind == "keywords":
            ids = vault.keys(limit=vault.count())
        else:
            dims = int(kind.split(":")[1])
            # VectorKVault has no keys API. TextVault.keys performs a plain
            # rowid SELECT on the existing table through the same native SQLite
            # library; its CREATE IF NOT EXISTS leaves the vec0 table intact.
            # Python sqlite3 must not open a live vault: separate SQLite copies
            # can invalidate each other's WAL/locking state on POSIX.
            reader = self._resources.open(
                TextVault, self._path, table=f"memory_vec_{dims}d"
            )
            try:
                ids = reader.keys(limit=vault.count())
            finally:
                self._resources.drop(reader)
        for row_id in ids:
            _, meta = vault.get_by_id(row_id)
            if isinstance(meta, dict) and meta.get("agent") == agent:
                vault.delete(row_id)

    def _prepare(self, agent: str, events: list[dict], kind: str, start_from: int):
        self._ensure_index_format(agent)
        key = f"{agent}:cursor:{kind}"
        checkpoint = self._state.get(key)
        revision = self._state.get(f"{key}:revision", 0)
        generation = self._generation(agent)
        reset = (
            checkpoint is None
            or checkpoint.get("generation") != generation
            or self._state.get(f"{key}:pending", False)
        )
        if not reset:
            try:
                blocks, next_state = extract_batch(
                    agent,
                    events,
                    checkpoint,
                    start_from,
                    all_branches=self._index_format == _ALL_BRANCH_INDEX_FORMAT,
                )
            except RebuildRequired:
                generation += 1
                self._state[f"{agent}:generation"] = generation
                reset = True
        if reset:
            self._state[f"{key}:pending"] = True
            self._clear_index(agent, kind)
            revision += 1
            self._state[f"{key}:revision"] = revision
            checkpoint = None
            blocks, next_state = extract_batch(
                agent,
                events,
                start_from=start_from,
                all_branches=self._index_format == _ALL_BRANCH_INDEX_FORMAT,
            )
        next_state["generation"] = generation
        return (
            _IndexBatch(
                checkpoint["count"] if checkpoint else 0,
                revision,
                generation,
                self._embedder,
                next_state,
            ),
            blocks,
        )

    def _finish_batch(self, agent: str, kind: str, batch: _IndexBatch) -> None:
        key = f"{agent}:cursor:{kind}"
        self._state[key] = batch.checkpoint
        self._state[f"{agent}:indexed_{kind}"] = batch.checkpoint["count"]
        self._state[f"{key}:revision"] = batch.revision + 1
        self._state[f"{key}:pending"] = False

    def index_events(
        self,
        agent: str,
        events: list[dict],
        start_from: int = 0,
    ) -> int:
        """Index new event blocks and return the number added.

        ``start_from`` supports incremental indexing and is advanced past the
        persisted per-agent watermark when necessary.
        """
        count = self._index_keywords(agent, events, start_from)
        if self._has_vectors and self._vec is not None:
            start, blocks = self._vector_batch(agent, events, start_from)
            vectors = (
                self._embedder.encode([b.content for b in blocks]) if blocks else []
            )
            self._commit_vectors(agent, start, len(events), blocks, vectors)
            count = max(count, len(blocks))
        return count

    def _index_keywords(
        self, agent: str, events: list[dict], start_from: int = 0
    ) -> int:
        batch, blocks = self._prepare(agent, events, "keywords", start_from)
        if (
            batch.start == len(events)
            and not blocks
            and not self._state.get(f"{agent}:cursor:keywords:pending")
        ):
            return 0
        self._state[f"{agent}:cursor:keywords:pending"] = True
        for block in blocks:
            self._fts.insert(block.content, _block_metadata(block))
        self._finish_batch(agent, "keywords", batch)
        return len(blocks)

    def _set_embedder(self, embedder: BaseEmbedder) -> None:
        self._embedder = embedder
        self._has_vectors = not isinstance(embedder, NullEmbedder)
        if self._has_vectors and embedder.dimensions > 0:
            previous = self._vec
            self._vec = self._resources.open(
                VectorKVault,
                self._path,
                table=f"memory_vec_{embedder.dimensions}d",
                dimensions=embedder.dimensions,
            )
            if previous is not None:
                self._resources.drop(previous)
            self._state["vec_dimensions"] = embedder.dimensions

    def _vector_batch(self, agent: str, events: list[dict], start_from: int = 0):
        return self._prepare(
            agent, events, f"vectors:{self._embedder.dimensions}", start_from
        )

    def _commit_vectors(
        self, agent, start: _IndexBatch, count, blocks, vectors
    ) -> None:
        kind = f"vectors:{self._embedder.dimensions}"
        key = f"{agent}:cursor:{kind}"
        if (
            start.embedder is not self._embedder
            or start.generation != self._generation(agent)
            or start.revision != self._state.get(f"{key}:revision", 0)
        ):
            raise RuntimeError("Memory index changed during embedding; retry search")
        if len(vectors) != len(blocks) or count != start.checkpoint["count"]:
            raise ValueError("Embedding result count does not match memory blocks")
        if (
            start.start == count
            and not blocks
            and not self._state.get(f"{key}:pending")
        ):
            return
        self._state[f"{key}:pending"] = True
        for vector, block in zip(vectors, blocks):
            self._vec.insert(vector, _block_metadata(block, include_content=True))
        self._finish_batch(agent, kind, start)

    def search(
        self,
        query: str,
        mode: str = "auto",
        k: int = 10,
        agent: str | None = None,
    ) -> list[SearchResult]:
        """Return relevance-ranked search results with an optional agent filter.

        ``auto`` selects hybrid search when embeddings are configured and keyword
        search otherwise. Explicit semantic search requires embeddings.
        """
        if mode == "auto":
            mode = "hybrid" if self._has_vectors else "fts"

        match mode:
            case "fts":
                return self._search_fts(query, k, agent)
            case "semantic":
                if not self._has_vectors:
                    # Explicit semantic mode must not silently degrade to keywords.
                    raise ValueError(
                        "semantic search needs an embedding model — "
                        "pass embedder= (see session.embedding."
                        "create_embedder) or run `kt embedding` first; "
                        "use mode='auto'/'hybrid' for graceful fallback"
                    )
                return self._search_semantic(query, k, agent)
            case "hybrid":
                if not self._has_vectors:
                    return self._search_fts(query, k, agent)
                return self._search_hybrid(query, k, agent)
            case _:
                raise ValueError(
                    f"Unknown search mode {mode!r} — expected 'fts', "
                    "'semantic', 'hybrid', or 'auto'"
                )

    def _search_fts(self, query: str, k: int, agent: str | None) -> list[SearchResult]:
        """FTS5 keyword search."""
        results = self._fts.search(query, k=k * 2)  # Agent filtering may discard rows.
        out = []
        for row_id, score, meta in results:
            if agent and meta.get("agent") != agent:
                continue
            text, _ = self._fts.get_by_id(row_id)
            out.append(
                SearchResult(
                    content=text or "",
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
            )
            if len(out) >= k:
                break
        return out

    def _search_semantic(
        self, query: str, k: int, agent: str | None
    ) -> list[SearchResult]:
        """Vector semantic search."""
        if not self._vec:
            return []

        query_vec = self._embedder.encode_one(query)
        return self._search_vector(query_vec, k, agent)

    def _search_vector(
        self, query_vec, k: int, agent: str | None
    ) -> list[SearchResult]:
        results = self._vec.search(query_vec, k=k * 2)
        out = []
        for _, distance, meta in results:
            if agent and meta.get("agent") != agent:
                continue
            content = meta.get("content", "")
            out.append(
                SearchResult(
                    content=content,
                    round_num=meta.get("round", 0),
                    block_num=meta.get("block", 0),
                    agent=meta.get("agent", ""),
                    block_type=meta.get("type", ""),
                    score=1.0 - distance,
                    ts=meta.get("ts", 0),
                    tool_name=meta.get("tool_name", ""),
                    channel=meta.get("channel", ""),
                    block_id=meta.get("block_id", ""),
                    **_source_result_metadata(meta),
                )
            )
            if len(out) >= k:
                break
        return out

    def _search_hybrid(
        self, query: str, k: int, agent: str | None
    ) -> list[SearchResult]:
        """Hybrid search: FTS + vector with reciprocal rank fusion."""
        fts_results = self._search_fts(query, k=k * 2, agent=agent)
        sem_results = self._search_semantic(query, k=k * 2, agent=agent)
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

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        fts_count = self._fts.count() if hasattr(self._fts, "count") else 0
        vec_count = self._vec.count() if self._vec is not None else 0
        return {
            "fts_blocks": fts_count,
            "vec_blocks": vec_count,
            "has_vectors": self._has_vectors,
            "dimensions": self._embedder.dimensions,
        }


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
    }


def _block_metadata(block: Block, include_content: bool = False) -> dict[str, Any]:
    """Build metadata dict for a block.

    FTS stores text as the key, while vector metadata must include display text.
    """
    meta: dict[str, Any] = {
        "round": block.round_num,
        "block": block.block_num,
        "agent": block.agent,
        "type": block.block_type,
        "ts": block.ts,
        "tool_name": block.tool_name,
        "channel": block.channel,
        "block_id": block.block_id,
        "event_id": block.event_id,
        "event_position": block.event_position,
        "part_index": block.part_index,
        "turn_index": block.turn_index,
        "branch_id": block.branch_id,
        "parent_branch_path": (
            [list(pair) for pair in block.parent_branch_path]
            if block.parent_branch_path is not None
            else None
        ),
    }
    if include_content:
        meta["content"] = block.content
    return meta
