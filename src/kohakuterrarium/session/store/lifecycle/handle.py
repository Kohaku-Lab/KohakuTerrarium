"""Open, path, artifact, flush and close surface of ``SessionStore``."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from kohakuterrarium.session.store.records.artifacts import (
    artifacts_dir_for,
    write_artifact_bytes,
)
from kohakuterrarium.session.store.events.counters import persist_event_counter
from kohakuterrarium.session.store.events.flush import EventFlushPipeline
from kohakuterrarium.session.store.lifecycle.tables import open_store_tables
from kohakuterrarium.session.store.lifecycle.table_policy import (
    TablePolicy,
    resolve_table_policy,
)
from kohakuterrarium.session.store.lifecycle.version import require_writable_format
from kohakuterrarium.utils.file.path import coerce_fs_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class StoreHandleMixin:
    """The file handle of a store: where it lives, whether it is open, how it closes."""

    # Tables assigned by ``open_store_tables``; ``fts`` is None when read-only.
    meta: Any
    state: Any
    events: Any
    channels: Any
    subagents: Any
    jobs: Any
    conversation: Any
    turn_rollup: Any
    fts: Any

    # Owned by the events part.
    _flush: EventFlushPipeline | None
    _flush_every_n_events: int
    _flush_every_n_seconds: float
    _reserved_event_id: int

    def _init_handle(
        self,
        path: str | Path,
        *,
        read_only: bool,
        writer_lock: bool,
        table_policy: Mapping[str, TablePolicy] | None,
    ) -> None:
        self._path = str(coerce_fs_path(path))
        # Read-only stores open existing files and never write to them.
        self._readonly = read_only
        if read_only:
            Path(self._path).resolve(strict=True)
        else:
            require_writable_format(self._path)
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        # Artifact storage is created only on first use.
        self._artifacts_dir: Path | None = None
        # Companion resources close in LIFO order before the store lock is released.
        self._companion_closers: list[Callable[[], None]] = []
        # Occupancy is enrolled before native open; the writer lock is optional.
        self._ready = False
        self._enroll_resources(writer_lock=writer_lock and not read_only)
        self._table_policy = resolve_table_policy(table_policy)

    @property
    def is_ready(self) -> bool:
        """Whether the tables finished opening (readers must wait until then)."""
        return self._ready

    def _open_tables(self) -> None:
        """Open every table; a writable store also gets its flush pipeline."""
        open_store_tables(
            self, self._path, read_only=self._readonly, policy=self._table_policy
        )
        if self._readonly:
            return
        self.events.enable_cache()
        self._flush = EventFlushPipeline(
            self.events,
            self.fts,
            every_n=self._flush_every_n_events,
            every_seconds=self._flush_every_n_seconds,
        )

    @property
    def session_id(self) -> str:
        """Session id from meta (preferred) or the filename stem."""
        stored = self.meta.get("session_id") if "session_id" in self.meta else None
        if stored:
            return str(stored)
        return Path(self._path).stem

    @property
    def path(self) -> str:
        """Path to the .kohakutr file."""
        return self._path

    @property
    def artifacts_dir(self) -> Path:
        """Session-local directory for binary artifacts (``<stem>.artifacts/``)."""
        if self._artifacts_dir is None:
            self._artifacts_dir = artifacts_dir_for(Path(self._path))
        return self._artifacts_dir

    def write_artifact(self, filename: str, data: bytes) -> Path:
        """Write raw bytes to ``artifacts_dir/<filename>``. Traversal-safe."""
        return write_artifact_bytes(self.artifacts_dir, filename, data)

    def register_companion_closer(self, closer: Callable[[], None]) -> None:
        # Registration is idempotent; companion resources close in reverse order.
        if closer not in self._companion_closers:
            self._companion_closers.append(closer)

    def flush(self) -> None:
        """Commit buffered events and FTS rows."""
        self._flush_events_cache()

    def checkpoint(self) -> None:
        """Commit buffered rows and checkpoint the WAL — without closing.

        Raw file copies omit cached and WAL-only rows. Call this before copying
        bytes from an open store so metadata and recent events are complete.
        """
        try:
            self._flush_events_cache()
        except Exception:  # pragma: no cover - the checkpoint still runs
            logger.warning("checkpoint: flush failed", exc_info=True)
        # One WAL serves every table of the file.
        try:
            self.events.checkpoint()
        except Exception:  # pragma: no cover - checkpoint is best effort
            logger.warning("checkpoint: WAL checkpoint failed", exc_info=True)

    def close(self, update_status: bool = True) -> None:
        """Idempotently flush and close the store and companion resources.

        Writable stores optionally transition to paused; read-only stores never
        mutate metadata.
        """
        try:
            if not self._begin_close():
                return
        except BaseException:
            if self._closed:
                self._finish_resource_lease(False)
            raise
        try:
            if self._readonly:
                update_status = False
            if self._flush is not None:
                # The clean-close counter is written only once every row is committed.
                try:
                    self._flush.close()
                    persist_event_counter(
                        self.state, self.global_max_event_id, self._reserved_event_id
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to commit buffered events on close",
                        error=str(e),
                        exc_info=True,
                    )
            if update_status:
                try:
                    self.update_status("paused")
                except Exception as e:
                    logger.warning(
                        "Failed to update session status on close",
                        error=str(e),
                        exc_info=True,
                    )
        except BaseException as primary:
            try:
                self._close_enrolled_resources()
            except BaseException as cleanup:
                raise primary from cleanup
            raise
        else:
            self._close_enrolled_resources()

    def __repr__(self) -> str:
        return f"SessionStore({self._path!r})"
