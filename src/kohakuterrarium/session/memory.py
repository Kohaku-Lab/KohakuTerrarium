"""Index session history for keyword, semantic, and hybrid search.

Events are grouped into rounds and searchable text, tool, trigger, or user blocks.
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

# Tool results are indexed up to this many chars so elided-but-≤256KB outputs
# stay recoverable via search_memory without bloating the FTS index.
TOOL_RESULT_INDEX_CHARS = 50_000

logger = get_logger(__name__)


@dataclass
class Block:
    """A searchable block within a round."""

    round_num: int
    block_num: int
    agent: str
    block_type: str
    content: str
    ts: float = 0.0
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
        """Return a compact elapsed-time label for the result timestamp."""
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
    ):
        self._path = db_path
        self._embedder = embedder or NullEmbedder()
        self._has_vectors = not isinstance(self._embedder, NullEmbedder)

        self._fts = TextVault(db_path, table="memory_fts")
        self._fts.enable_auto_pack()

        self._state = KVault(db_path, table="memory_state")
        self._state.enable_auto_pack()

        # Dimension-specific tables prevent incompatible embedding models from mixing.
        self._vec: VectorKVault | None = None
        if self._has_vectors and self._embedder.dimensions > 0:
            dims = self._embedder.dimensions
            self._vec = VectorKVault(
                db_path, table=f"memory_vec_{dims}d", dimensions=dims
            )
            self._state["vec_dimensions"] = dims
        elif not self._has_vectors:
            # Existing vector tables remain queryable without a configured embedder.
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

    def close(self) -> None:
        """Release the native SQLite handles this index opened.

        These indexes own handles separate from ``SessionStore``. Native references
        are dropped explicitly because public vault close paths can retain Windows
        file locks until garbage collection.
        """
        state = getattr(self, "_state", None)
        if state is not None and hasattr(state, "close"):
            try:
                state.close()
            except Exception as e:  # pragma: no cover - cleanup continues per handle
                logger.warning("memory state close failed", error=str(e), exc_info=True)
        for table, attr in (
            (getattr(self, "_state", None), "_inner"),
            (getattr(self, "_fts", None), "_vault"),
            (getattr(self, "_vec", None), "_vault"),
        ):
            if table is None:
                continue
            try:
                delattr(table, attr)
            except AttributeError:
                pass

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
        already_indexed = self._get_indexed_count(agent)

        # Rebuild both indexes when vector support is added after FTS-only indexing.
        vec_needs_rebuild = (
            self._vec is not None and self._vec.count() == 0 and already_indexed > 0
        )
        if vec_needs_rebuild:
            already_indexed = 0
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

        for block in blocks:
            if block.content.strip():
                metadata = _block_metadata(block)
                self._fts[block.content] = metadata

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
    }
    if include_content:
        meta["content"] = block.content
    return meta


def _content_to_text(content: Any) -> str:
    """Flatten an event's ``content`` to a single searchable string.

    Strings pass through, multimodal parts contribute searchable text or stable
    attachment markers, and dictionaries are handled as single parts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                elif isinstance(part.get("content"), str):
                    chunks.append(part["content"])
                else:
                    kind = part.get("type") or ""
                    if kind in ("image_url", "image"):
                        chunks.append("[image]")
                    elif kind == "file":
                        chunks.append("[file]")
        return " ".join(c for c in chunks if c)
    if isinstance(content, dict):
        return _content_to_text([content])
    return "" if content is None else str(content)


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
            content = _content_to_text(evt.get("content", ""))
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
            content = _content_to_text(evt.get("content", ""))
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
            # Stream chunks remain separate searchable blocks in event order.
            content = _content_to_text(evt.get("content", ""))
            # Paragraph splits improve retrieval precision for long responses.
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
            output = _content_to_text(evt.get("output", ""))
            error = _content_to_text(evt.get("error", ""))
            content = f"[result:{name}] {error or output}"
            if content.strip() and len(content) > 20:
                blocks.append(
                    Block(
                        round_num=round_num,
                        block_num=block_num,
                        agent=agent,
                        block_type="tool",
                        content=content[:TOOL_RESULT_INDEX_CHARS],
                        ts=ts,
                        tool_name=name,
                    )
                )
                block_num += 1

        elif etype == "processing_end":
            in_round = False

    return blocks
