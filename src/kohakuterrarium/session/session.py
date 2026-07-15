"""Provide asynchronous session forking and optional agent attachment."""

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.session.attachment_service import attach_agent_to_session
from kohakuterrarium.session.attachment_service import detach_agent_from_session
from kohakuterrarium.session.attachment_service import get_attach_state
from kohakuterrarium.session.errors import NotAttachedError
from kohakuterrarium.session.migrations import path_for_version
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.version import FORMAT_VERSION
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)


def _derive_fork_path(parent_path: str, name: str | None) -> Path:
    """Build the on-disk path for a forked child session.

    The child reuses the parent directory, adds the supplied name or a UUID to
    the stem, and targets the current format's versioned path.
    """
    parent = Path(parent_path)
    bare_stem = parent.stem
    # Versioned paths retain ``.kohakutr`` in ``Path.stem``.
    suffix = name or f"fork-{uuid.uuid4().hex[:8]}"
    # The fork tag belongs before the session extension.
    if parent.suffixes and parent.suffixes[0] == ".kohakutr":
        base = parent.name.split(".kohakutr", 1)[0]
        child_bare = parent.parent / f"{base}-{suffix}.kohakutr"
    else:
        child_bare = parent.parent / f"{bare_stem}-{suffix}.kohakutr"
    return path_for_version(child_bare, FORMAT_VERSION)


class Session:
    """Wrap a session store with optional agent attachment and async forking.

    Agent-less instances remain useful as data handles for persisted forks.
    """

    def __init__(
        self,
        store: SessionStore,
        *,
        agent: "Agent | None" = None,
        name: str | None = None,
    ) -> None:
        self._store = store
        self._agent = agent
        self._name = name or store.session_id
        self._fork_lock = asyncio.Lock()

    @property
    def store(self) -> SessionStore:
        """Underlying synchronous store."""
        return self._store

    @property
    def agent(self) -> "Agent | None":
        """Attached agent, if any."""
        return self._agent

    @property
    def name(self) -> str:
        """Display name (defaults to the store's ``session_id``)."""
        return self._name

    @property
    def path(self) -> str:
        """Path to the backing ``.kohakutr`` file."""
        return self._store.path

    def _pending_job_ids(self) -> set[str]:
        """Collect in-flight call ids from the attached agent, if any.

        Fork stability requires identifiers for active executor jobs. Agent-less
        sessions have no in-flight work.
        """
        agent = self._agent
        if agent is None:
            return set()

        pending: set[str] = set()
        executor = getattr(agent, "executor", None)
        if executor is None:
            return pending

        # Duck typing avoids coupling session forks to one executor job class.
        list_fn = getattr(executor, "list_pending_jobs", None)
        jobs: list[Any]
        if callable(list_fn):
            try:
                jobs = list(list_fn())
            except Exception as e:
                logger.warning(
                    "Session.fork failed to list executor pending jobs",
                    error=str(e),
                    exc_info=True,
                )
                jobs = []
        else:
            jobs = []
        for job in jobs:
            call_id = (
                getattr(job, "call_id", None)
                or getattr(job, "job_id", None)
                or (isinstance(job, dict) and (job.get("call_id") or job.get("job_id")))
            )
            if call_id:
                pending.add(str(call_id))
        return pending

    def attach_agent(self, agent: "Agent", role: str) -> None:
        """Attach ``agent`` to this session under ``role``.

        An agent may attach to only one session at a time.
        """
        attach_agent_to_session(agent, self, role)

    def detach_agent(self, agent: "Agent") -> None:
        """Detach ``agent`` from this session.

        Raise :class:`NotAttachedError` when the agent belongs to another session
        or has no attachment.
        """
        state = get_attach_state(agent)
        if state is None or state.get("session") is not self:
            raise NotAttachedError(
                "Agent is not attached to this Session.",
            )
        detach_agent_from_session(agent)

    async def fork(
        self,
        at_event_id: int,
        mutate: Callable[[dict], dict] | None = None,
        name: str | None = None,
    ) -> "Session":
        """Fork into a new :class:`Session` rooted at ``at_event_id``.

        Forks serialize parent lineage updates and run the synchronous copy in a
        worker thread so large histories do not block the event loop.
        """
        child_name = name or f"{self._name}-fork-{uuid.uuid4().hex[:8]}"
        target = _derive_fork_path(self._store.path, name=child_name)

        async with self._fork_lock:
            pending = self._pending_job_ids()
            child_store = await asyncio.to_thread(
                self._store.fork,
                str(target),
                at_event_id=at_event_id,
                mutate=mutate,
                name=child_name,
                pending_job_ids=pending,
            )

        return Session(child_store, agent=None, name=child_name)
