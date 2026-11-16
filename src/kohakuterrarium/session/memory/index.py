"""Index session history for keyword, semantic, and hybrid search.

Events are grouped into rounds and searchable text, tool, trigger, or user blocks.
"""

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

from kohakuterrarium.session.memory.query import (
    MemorySearchMixin,
    SearchResult as SearchResult,
    _ALL_BRANCH_INDEX_FORMAT,
    _SELECTED_INDEX_FORMAT,
)


@dataclass
class _IndexBatch:
    start: int
    revision: int
    generation: int
    embedder: object
    checkpoint: dict


class SessionMemory(MemorySearchMixin):
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
