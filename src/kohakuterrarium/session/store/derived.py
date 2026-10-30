"""Operations derived from stored rows: full-text search, token usage, fork."""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.session.store.fork import perform_fork
from kohakuterrarium.session.token_views import (
    token_usage as _token_usage_impl,
    token_usage_all_loops as _token_usage_all_loops_impl,
)
from kohakuterrarium.session.vault_handles import dispose
from kohakuterrarium.session.vault_read import open_read_text
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.session.store import SessionStore

logger = get_logger(__name__)


class StoreDerivedMixin:
    """Thin entry points that delegate to search, usage and fork implementations."""

    # Owned by the handle part.
    _path: str
    fts: Any

    def search(self, query: str, k: int = 10) -> list[dict]:
        """Search session content via FTS5 (BM25 keyword search).

        Queued FTS rows commit first. Returns dicts with score and metadata.
        """
        results = []
        try:
            self._flush_events_cache()
            fts = self.fts
            if fts is None:
                fts = open_read_text(Path(self._path), "fts", snapshot=False)
            if fts is None:
                return results
            try:
                for doc_id, score, meta in fts.search(query, k=k):
                    results.append({"doc_id": doc_id, "score": score, "meta": meta})
            finally:
                if fts is not self.fts:
                    dispose(fts)
        except Exception as e:
            logger.warning("FTS search failed", error=str(e))
        return results

    def fork(
        self,
        target_path: str,
        *,
        at_event_id: int,
        mutate: Callable[[dict], dict] | None = None,
        name: str | None = None,
        pending_job_ids: set[str] | None = None,
    ) -> "SessionStore":
        """Create an independent child session through ``at_event_id``."""
        return perform_fork(
            self,
            target_path,
            at_event_id=at_event_id,
            mutate=mutate,
            name=name,
            pending_job_ids=pending_job_ids,
        )

    def token_usage(
        self,
        agent: str | None = None,
        *,
        include_subagents: bool = False,
        include_attached: bool = False,
        by_turn: bool = False,
    ) -> dict[str, Any]:
        """Return token counters for ``agent``.

        The agent namespace is required. Optional mappings expose sub-agent and
        attached-agent counters, while ``by_turn`` uses rollups with event-based
        fallback.
        """
        return _token_usage_impl(
            self,
            agent,
            include_subagents=include_subagents,
            include_attached=include_attached,
            by_turn=by_turn,
        )

    def token_usage_all_loops(self) -> list[tuple[str, dict[str, int]]]:
        """Flat enumeration of every controller loop in the session.

        Paths identify main, sub-agent, and attached-agent loops. Each usage
        dictionary contains only that loop's counters, without aggregation.
        """
        return _token_usage_all_loops_impl(self)
