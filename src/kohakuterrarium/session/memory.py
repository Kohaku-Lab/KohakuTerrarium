"""
Session memory: indexing and search over session event history.

Indexes session events into searchable blocks with FTS5 (keyword)
and vector (semantic) search. Two-level hierarchy:

  Round: one processing cycle (user input -> agent response)
  Block: a segment within a round (text paragraph, tool call, trigger)

Usage:
    memory = SessionMemory(store, embedder)
    memory.index_events("root")           # index all unindexed events
    results = memory.search("auth bug", mode="hybrid", k=5)
"""

import time
from dataclasses import dataclass, field
from typing import Any

from kohakuvault import KVault, TextVault, VectorKVault

from kohakuterrarium.session.embedding import BaseEmbedder, NullEmbedder
from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    select_live_event_ids,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Block:
    """A searchable block within a round."""

    round_num: int
    block_num: int
    agent: str
    block_type: str  # "text", "tool", "trigger", "user"
    content: str  # text content for indexing
    ts: float = 0.0
    # Extra metadata
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    channel: str = ""


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

    @property
    def age_str(self) -> str:
        """Human-readable time ago string."""
        if self.ts <= 0:
            return ""
        elapsed = time.time() - self.ts
        if elapsed < 60:
            return f"{int(elapsed)}s ago"
        if elapsed < 3600:
            return f"{int(elapsed / 60)}m ago"
        return f"{elapsed / 3600:.1f}h ago"


class SessionMemory:
    """Search index over session event history.

    Manages FTS5 (keyword) and vector (semantic) indexes for a session.
    Indexes are stored in the same .kohakutr SQLite file as the session.
    """

    def __init__(
        self,
        db_path: str,
        embedder: BaseEmbedder | None = None,
        store: Any = None,
    ):
        self._path = db_path
        self._store = store  # SessionStore, for looking up full event content
        self._embedder = embedder or NullEmbedder()
        self._has_vectors = not isinstance(self._embedder, NullEmbedder)

        # FTS index (always available)
        self._fts = TextVault(db_path, table="memory_fts")
        self._fts.enable_auto_pack()

        # Index state tracking
        self._state = KVault(db_path, table="memory_state")
        self._state.enable_auto_pack()

        # Vector index (only if embedder is configured)
        # Table name includes dimensions to avoid mismatch on model change.
        # Dimensions are saved in memory_state for discovery by search.
        self._vec: VectorKVault | None = None
        if self._has_vectors and self._embedder.dimensions > 0:
            dims = self._embedder.dimensions
            self._vec = VectorKVault(
                db_path, table=f"memory_vec_{dims}d", dimensions=dims
            )
            self._state["vec_dimensions"] = dims
        elif not self._has_vectors:
            # No embedder: try to open existing vector table (for search-only)
            try:
                saved_dims = self._state["vec_dimensions"]
                if saved_dims and saved_dims > 0:
                    self._vec = VectorKVault(
                        db_path,
                        table=f"memory_vec_{saved_dims}d",
                        dimensions=saved_dims,
                    )
            except (KeyError, Exception):
                pass

    @property
    def has_vectors(self) -> bool:
        return self._vec is not None

    def _get_indexed_count(self, agent: str) -> int:
        """Get number of events already indexed for an agent."""
        try:
            return self._state[f"{agent}:indexed_events"]
        except KeyError:
            return 0

    def _set_indexed_count(self, agent: str, count: int) -> None:
        self._state[f"{agent}:indexed_events"] = count

    def _clear_fts(self, agent: str) -> None:
        """Clear FTS entries for an agent (before full re-index)."""
        try:
            to_delete = []
            for row_id in self._fts.keys():
                _, val = self._fts.get_by_id(row_id)
                if isinstance(val, dict) and val.get("agent") == agent:
                    to_delete.append(row_id)
            for row_id in to_delete:
                self._fts.delete(row_id)
            logger.debug("Cleared FTS entries", agent=agent, count=len(to_delete))
        except Exception as e:
            logger.warning("Failed to clear FTS", error=str(e))

    # ── Indexing ───────────────────────────────────────────────

    def index_events(
        self,
        agent: str,
        events: list[dict],
        start_from: int = 0,
    ) -> int:
        """Index session events into FTS + vector search.

        Args:
            agent: Agent name
            events: List of event dicts from SessionStore
            start_from: Skip first N events (for incremental indexing)

        Returns:
            Number of new blocks indexed
        """
        already_indexed = self._get_indexed_count(agent)

        # If vectors are enabled but empty, force full re-index
        # (previous indexing may have been FTS-only)
        vec_needs_rebuild = (
            self._vec is not None and self._vec.count() == 0 and already_indexed > 0
        )
        if vec_needs_rebuild:
            already_indexed = 0
            # Clear stale FTS entries to avoid duplicates
            self._clear_fts(agent)

        if start_from < already_indexed:
            start_from = already_indexed

        if start_from >= len(events):
            return 0

        new_events = events[start_from:]
        blocks = _extract_blocks(agent, new_events, start_from)

        if not blocks:
            self._set_indexed_count(agent, len(events))
            return 0

        # Index into FTS (text is key, metadata points to round/block)
        for block in blocks:
            if block.content.strip():
                metadata = _block_metadata(block)
                self._fts[block.content] = metadata

        # Index into vectors (batch)
        if self._has_vectors and self._vec is not None:
            vec_texts = [b.content for b in blocks if b.content.strip()]
            vec_metas = [
                _block_metadata(b, include_content=True)
                for b in blocks
                if b.content.strip()
            ]
            if vec_texts:
                vectors = self._embedder.encode(vec_texts)
                for v, m in zip(vectors, vec_metas):
                    self._vec.insert(v, m)
                logger.debug(
                    "Vectors indexed",
                    count=len(vectors),
                    vec_count=self._vec.count(),
                )

        self._set_indexed_count(agent, len(events))
        logger.info(
            "Indexed session blocks",
            agent=agent,
            new_blocks=len(blocks),
            total_events=len(events),
        )
        return len(blocks)

    # ── Search ─────────────────────────────────────────────────

    def search(
        self,
        query: str,
        mode: str = "auto",
        k: int = 10,
        agent: str | None = None,
    ) -> list[SearchResult]:
        """Search session memory.

        Args:
            query: Search query text
            mode: "fts" (keyword), "semantic" (vector), "hybrid", or "auto"
            k: Max results
            agent: Filter by agent name (optional)

        Returns:
            List of SearchResult, sorted by relevance
        """
        if mode == "auto":
            # Short queries with identifiers -> fts, natural language -> hybrid
            mode = "hybrid" if self._has_vectors else "fts"

        match mode:
            case "fts":
                return self._search_fts(query, k, agent)
            case "semantic":
                if not self._has_vectors:
                    logger.warning("No embedding model, falling back to FTS")
                    return self._search_fts(query, k, agent)
                return self._search_semantic(query, k, agent)
            case "hybrid":
                if not self._has_vectors:
                    return self._search_fts(query, k, agent)
                return self._search_hybrid(query, k, agent)
            case _:
                return self._search_fts(query, k, agent)

    def _search_fts(self, query: str, k: int, agent: str | None) -> list[SearchResult]:
        """FTS5 keyword search."""
        results = self._fts.search(query, k=k * 2)  # over-fetch for filtering
        out = []
        for row_id, score, meta in results:
            if agent and meta.get("agent") != agent:
                continue
            # Get the actual text content via get_by_id
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

        # Reciprocal Rank Fusion (RRF)
        scores: dict[str, float] = {}
        result_map: dict[str, SearchResult] = {}

        for rank, r in enumerate(fts_results):
            key = f"{r.agent}:r{r.round_num}:b{r.block_num}"
            scores[key] = scores.get(key, 0) + 1.0 / (60 + rank)
            result_map[key] = r

        for rank, r in enumerate(sem_results):
            key = f"{r.agent}:r{r.round_num}:b{r.block_num}"
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


# ── Block extraction ───────────────────────────────────────────


def _block_metadata(block: Block, include_content: bool = False) -> dict[str, Any]:
    """Build metadata dict for a block.

    FTS: no content needed (text is the key).
    Vector: include content (needed for display, since vectors have no text key).
    """
    meta: dict[str, Any] = {
        "round": block.round_num,
        "block": block.block_num,
        "agent": block.agent,
        "type": block.block_type,
        "ts": block.ts,
        "tool_name": block.tool_name,
        "channel": block.channel,
    }
    if include_content:
        meta["content"] = block.content
    return meta


def _extract_blocks(
    agent: str, events: list[dict], event_offset: int = 0
) -> list[Block]:
    """Extract searchable blocks from session events."""
    events = dedupe_adjacent_duplicate_events(events)
    live_ids = select_live_event_ids(events)
    blocks: list[Block] = []
    round_num = 0
    block_num = 0
    in_round = False

    for i, evt in enumerate(events):
        etype = evt.get("type", "")
        ts = evt.get("ts", 0)
        eid = evt.get("event_id")
        if isinstance(eid, int) and eid not in live_ids:
            continue

        if etype == "user_input":
            round_num += 1
            block_num = 0
            in_round = True
            content = evt.get("content", "")
            if content.strip():
                blocks.append(
                    Block(
                        round_num=round_num,
                        block_num=block_num,
                        agent=agent,
                        block_type="user",
                        content=content,
                        ts=ts,
                    )
                )
                block_num += 1

        elif etype == "trigger_fired":
            round_num += 1
            block_num = 0
            in_round = True
            channel = evt.get("channel", "")
            content = evt.get("content", "")
            label = f"[trigger:{channel}] {content}" if channel else content
            if label.strip():
                blocks.append(
                    Block(
                        round_num=round_num,
                        block_num=block_num,
                        agent=agent,
                        block_type="trigger",
                        content=label,
                        ts=ts,
                        channel=channel,
                    )
                )
                block_num += 1

        elif etype in ("text", "text_chunk") and in_round:
            # ``text_chunk`` is Wave C's per-chunk streaming format.
            # Index the same way; FTS treats consecutive chunks as
            # separate blocks (callers can reassemble via event_id
            # ordering if needed).
            content = evt.get("content", "")
            # Split long text on double newlines for finer-grained blocks
            paragraphs = content.split("\n\n") if len(content) > 300 else [content]
            for para in paragraphs:
                if para.strip():
                    blocks.append(
                        Block(
                            round_num=round_num,
                            block_num=block_num,
                            agent=agent,
                            block_type="text",
                            content=para.strip(),
                            ts=ts,
                        )
                    )
                    block_num += 1

        elif etype == "tool_call" and in_round:
            name = evt.get("name", "")
            args = evt.get("args", {})
            # Build searchable text from tool call
            args_text = " ".join(
                f"{k}={v}" for k, v in args.items() if k != "_tool_call_id"
            )
            content = f"[tool:{name}] {args_text}"
            blocks.append(
                Block(
                    round_num=round_num,
                    block_num=block_num,
                    agent=agent,
                    block_type="tool",
                    content=content[:1000],
                    ts=ts,
                    tool_name=name,
                    tool_args=args,
                )
            )
            block_num += 1

        elif etype == "tool_result" and in_round:
            name = evt.get("name", "")
            output = evt.get("output", "")
            error = evt.get("error", "")
            content = f"[result:{name}] {error or output}"
            if content.strip() and len(content) > 20:
                blocks.append(
                    Block(
                        round_num=round_num,
                        block_num=block_num,
                        agent=agent,
                        block_type="tool",
                        content=content[:2000],
                        ts=ts,
                        tool_name=name,
                    )
                )
                block_num += 1

        elif etype == "processing_end":
            in_round = False

    return blocks
