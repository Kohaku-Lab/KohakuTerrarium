"""Index session history for keyword, semantic, and hybrid search.

Events are grouped into rounds and searchable text, tool, trigger, or user blocks.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kohakuvault import KVault, TextVault, VectorKVault

from kohakuterrarium.session.memory.embedding import (
    BaseEmbedder,
    NullEmbedder,
    embedder_identity,
)
from kohakuterrarium.session.memory.resources import MemoryResources
from kohakuterrarium.session.memory.lifecycle import MemoryIndexLifecycle
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
    reset: bool = False
    was_pending: bool = False
    previous: dict | None = None


class SessionMemory(MemoryIndexLifecycle, MemorySearchMixin):
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
        # Every alias to one physical SessionStore must contend on identical
        # memory build/mutation lock files and open the same native resources.
        self._path = str(Path(db_path).resolve())
        self._embedder = embedder or NullEmbedder()
        self._has_vectors = not isinstance(self._embedder, NullEmbedder)
        self._vector_identity = (
            embedder_identity(self._embedder) if self._has_vectors else None
        )

        self._fts_build_lock = None
        self._vector_build_lock = None
        self._resources = MemoryResources(self._path)
        try:
            self._open_indexes(self._path)
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
        try:
            self._resources.close()
        finally:
            self._release_build_locks()

    def _get_indexed_count(self, agent: str) -> int:
        """Get number of events already indexed for an agent."""
        return self._state.get(
            f"{agent}:indexed_keywords", self._state.get(f"{agent}:indexed_events", 0)
        )

    def _generation(self, agent: str) -> int:
        return self._state.get(f"{agent}:generation", 0)

    def _prepare(self, agent: str, events: list[dict], kind: str, start_from: int):
        """Plan a batch without changing cursors, manifest, or physical rows.

        The caller stages a BUILDING manifest *before* applying this plan.
        Embedding may run without the short mutation lock, but its cursor is
        fenced at commit against this planned source snapshot.
        """
        manifest = self._manifest()
        key = f"{agent}:cursor:{kind}"
        checkpoint = self._state.get(key)
        was_pending = bool(self._state.get(f"{key}:pending", False))
        revision = self._state.get(f"{key}:revision", 0)
        generation = self._generation(agent)
        reset = (
            checkpoint is None
            or checkpoint.get("index_epoch") != manifest["epoch"]
            or checkpoint.get("generation") != generation
            or was_pending
            or (
                kind.startswith("vectors:")
                and checkpoint is not None
                and (
                    checkpoint.get("embedder_identity") != self._vector_identity
                    or checkpoint.get("vector_generation")
                    != manifest["vector_generation"]
                    or (
                        checkpoint.get("keyword_revision")
                        != self._state.get(f"{agent}:cursor:keywords:revision", 0)
                        and (
                            # Ordinary FTS appends advance its revision without
                            # invalidating the previously embedded prefix.
                            # A missing revision or same-length FTS rewrite
                            # cannot prove that old vectors still match.
                            "keyword_revision" not in checkpoint
                            or checkpoint.get("count") >= len(events)
                        )
                    )
                )
            )
            or (
                self._index_format == _ALL_BRANCH_INDEX_FORMAT
                and checkpoint.get("source_refs_complete") is False
                and events
                and bool(getattr(events[-1], "source_ref", ""))
            )
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
                reset = True
        if reset:
            blocks, next_state = extract_batch(
                agent,
                events,
                start_from=start_from,
                all_branches=self._index_format == _ALL_BRANCH_INDEX_FORMAT,
            )
        next_state["generation"] = generation
        next_state["index_epoch"] = manifest["epoch"]
        if kind.startswith("vectors:"):
            next_state["embedder_identity"] = self._vector_identity
            next_state["vector_generation"] = manifest["vector_generation"]
            # Bind the vector's source to the exact published FTS checkpoint,
            # not just an event count that may be reused after a rewrite.
            next_state["keyword_revision"] = self._state.get(
                f"{agent}:cursor:keywords:revision", 0
            )
        batch = _IndexBatch(
            checkpoint["count"] if checkpoint and not reset else 0,
            revision + int(reset),
            generation,
            self._embedder,
            next_state,
            reset=reset,
            was_pending=was_pending,
            previous=checkpoint,
        )
        return batch, blocks

    def _stage_batch(self, agent: str, kind: str, batch: _IndexBatch) -> None:
        """Apply a planned destructive reset after the build barrier is set."""
        if not batch.reset:
            return
        key = f"{agent}:cursor:{kind}"
        self._state[f"{key}:pending"] = True
        epoch = self._manifest()["epoch"]
        globally_cleared = (
            getattr(
                self,
                (
                    "_vec_cleared_epoch"
                    if kind.startswith("vectors:")
                    else "_fts_cleared_epoch"
                ),
                None,
            )
            == epoch
        )
        if (
            batch.was_pending
            or (
                isinstance(batch.previous, dict)
                and batch.previous.get("index_epoch") == epoch
            )
            or (
                not globally_cleared
                and self._state.get(f"{agent}:indexed_{kind}", 0) > 0
            )
        ):
            self._clear_index(agent, kind)
        self._state[f"{key}:revision"] = batch.revision
        if self._generation(agent) != batch.generation:
            self._state[f"{agent}:generation"] = batch.generation

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
        with self._mutation():
            self._ensure_index_format(agent)
            batch, blocks = self._prepare(agent, events, "keywords", start_from)
            changed = batch.reset or batch.start != len(events) or bool(blocks)
            if changed:
                self._begin_fts_append(agent, changed=True)
                self._stage_batch(agent, "keywords", batch)
                self._state[f"{agent}:cursor:keywords:pending"] = True
                for block in blocks:
                    self._fts.insert(block.content, _block_metadata(block))
                self._finish_batch(agent, "keywords", batch)
            self._finish_fts(agent, changed)
            return len(blocks)

    def _set_embedder(self, embedder: BaseEmbedder) -> None:
        self._embedder = embedder
        self._has_vectors = not isinstance(embedder, NullEmbedder)
        self._vector_identity = (
            embedder_identity(embedder) if self._has_vectors else None
        )
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
        with self._mutation():
            self._ensure_index_format(agent)
            kind = f"vectors:{self._embedder.dimensions}"
            manifest = self._manifest()
            # A Ready vector may still need a destructive per-agent rebuild
            # (e.g. source-reference migration). Plan before deciding whether
            # it is safe to leave the global vector status at Ready.
            planned_change = False
            if type(manifest.get("vector_generation")) is int:
                preview, blocks = self._prepare(agent, events, kind, start_from)
                planned_change = (
                    preview.reset or preview.start != len(events) or bool(blocks)
                )
            self._begin_vector(agent, planned_change=planned_change)
            batch, blocks = self._prepare(agent, events, kind, start_from)
            self._stage_batch(agent, kind, batch)
            return batch, blocks

    def _commit_vectors(
        self, agent, start: _IndexBatch, count, blocks, vectors
    ) -> None:
        with self._mutation():
            kind = f"vectors:{self._embedder.dimensions}"
            key = f"{agent}:cursor:{kind}"
            if (
                start.checkpoint.get("index_epoch") != self._manifest()["epoch"]
                or start.checkpoint.get("vector_generation")
                != self._manifest()["vector_generation"]
                or start.checkpoint.get("keyword_revision")
                != self._state.get(f"{agent}:cursor:keywords:revision", 0)
                or start.embedder is not self._embedder
                or start.generation != self._generation(agent)
                or start.revision != self._state.get(f"{key}:revision", 0)
            ):
                raise RuntimeError(
                    "Memory index changed during embedding; retry search"
                )
            if len(vectors) != len(blocks) or count != start.checkpoint["count"]:
                raise ValueError("Embedding result count does not match memory blocks")
            keywords = self._state.get(f"{agent}:cursor:keywords")
            if (
                not isinstance(keywords, dict)
                or keywords.get("index_epoch") != start.checkpoint["index_epoch"]
                or keywords.get("count") != start.checkpoint["count"]
                or keywords.get("anchor") != start.checkpoint["anchor"]
                or keywords.get("source_refs_complete", True)
                != start.checkpoint.get("source_refs_complete", True)
            ):
                raise RuntimeError("Memory keyword snapshot changed during embedding")
            if (
                start.start == count
                and not blocks
                and not self._state.get(f"{key}:pending")
            ):
                if self._manifest()["vector"] != "ready":
                    self._finish_vector(agent)
                return
            self._state[f"{key}:pending"] = True
            for vector, block in zip(vectors, blocks):
                self._vec.insert(vector, _block_metadata(block, include_content=True))
            self._finish_batch(agent, kind, start)
            self._finish_vector(agent)

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
        "source_fingerprint": block.source_fingerprint,
        "source_ref": block.source_ref,
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
