"""Persist session metadata, events, state, and indexes in KohakuVault.

``SessionStore`` is assembled here from the parts in this package; see
``README.md`` for the part map and the assembly rules.
"""

from collections.abc import Mapping
from pathlib import Path

from kohakuterrarium.session.branch_index import BranchIndexMixin
from kohakuterrarium.session.store.derived import StoreDerivedMixin
from kohakuterrarium.session.store.events.append import EventAppendMixin
from kohakuterrarium.session.store.events.read import EventReadMixin
from kohakuterrarium.session.store.lifecycle.affinity import StoreAffinityMixin
from kohakuterrarium.session.store.lifecycle.handle import StoreHandleMixin
from kohakuterrarium.session.store.lifecycle.resources import StoreResourceMixin
from kohakuterrarium.session.store.meta import StoreMetaMixin
from kohakuterrarium.session.store.records.channels import ChannelRecordsMixin
from kohakuterrarium.session.store.records.conversation import (
    ConversationRecordsMixin,
)
from kohakuterrarium.session.store.records.jobs import JobRecordsMixin
from kohakuterrarium.session.store.records.subagents import SubagentRecordsMixin
from kohakuterrarium.session.table_policy import TablePolicy
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class SessionStore(
    StoreAffinityMixin,
    StoreResourceMixin,
    StoreHandleMixin,
    EventAppendMixin,
    EventReadMixin,
    ConversationRecordsMixin,
    ChannelRecordsMixin,
    SubagentRecordsMixin,
    JobRecordsMixin,
    StoreMetaMixin,
    StoreDerivedMixin,
    BranchIndexMixin,
):
    """Persistent session storage backed by KohakuVault.

    One ``.kohakutr`` file contains metadata, per-agent state, append-only
    events, channel history, child-run records, conversation caches, turn
    rollups, job records, and full-text search data.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        flush_every_n_events: int | None = None,
        flush_every_n_seconds: float | None = None,
        writer_lock: bool = False,
        read_only: bool = False,
        table_policy: Mapping[str, TablePolicy] | None = None,
    ) -> None:
        self._init_affinity()
        self._init_handle(
            path,
            read_only=read_only,
            writer_lock=writer_lock,
            table_policy=table_policy,
        )
        self._init_events(flush_every_n_events, flush_every_n_seconds)
        self._init_channels()
        self._init_subagents()

        # Construction failures must not strand tables or the writer lock.
        try:
            self._open_tables()
            self._restore_counters()
        except BaseException:
            self._discard_failed_open()
            raise
        self._ready = True
        logger.debug("SessionStore opened", path=self._path)

    def _restore_counters(self) -> None:
        """Restore every sequence counter from the opened tables."""
        self._restore_event_counters()
        self._restore_channel_counters()
        self._restore_subagent_counters()

        if self._event_seq or self._channel_seq:
            logger.debug(
                "Counters restored",
                event_agents=list(self._event_seq.keys()),
                channel_count=len(self._channel_seq),
            )

    @classmethod
    def open_readonly(cls, path: "str | Path") -> "SessionStore":
        """Open an existing session read-only: no writes, no sidecars on close.

        Listing and viewer consumers use this mode to preserve recency.
        """
        return cls(path, read_only=True)
