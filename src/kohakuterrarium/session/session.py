"""Async wrapper around a running agent + :class:`SessionStore`.

Wave E only needs the fork-related surface; the rest of the Session
lifecycle (``process_event``, attach / detach, token views) is the
concern of later waves. The API surface is kept deliberately minimal
and extensible so Waves F / G can bolt on methods without reshaping
the constructor.
"""

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.session.migrations import path_for_version
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.version import FORMAT_VERSION
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.core.agent import Agent

logger = get_logger(__name__)


def _derive_fork_path(parent_path: str, name: str | None) -> Path:
    """Build the on-disk path for a forked child session.

    Uses :func:`path_for_version` so the child lands on the current
    ``FORMAT_VERSION`` slot (``<stem>.kohakutr.v2`` today). The stem is
    the parent stem suffixed with the supplied ``name`` (or a short
    uuid). Parent directory is reused.
    """
    parent = Path(parent_path)
    bare_stem = parent.stem
    # Strip a trailing ``.kohakutr`` if present — Path.stem of
    # ``alice.kohakutr.v2`` keeps ``.kohakutr`` in place, but
    # path_for_version strips the version suffix for us.
    suffix = name or f"fork-{uuid.uuid4().hex[:8]}"
    # Insert the fork tag before the ``.kohakutr`` extension so the
    # result stays a well-formed session filename.
    if parent.suffixes and parent.suffixes[0] == ".kohakutr":
        base = parent.name.split(".kohakutr", 1)[0]
        child_bare = parent.parent / f"{base}-{suffix}.kohakutr"
    else:
        child_bare = parent.parent / f"{bare_stem}-{suffix}.kohakutr"
    return path_for_version(child_bare, FORMAT_VERSION)


class Session:
    """Async facade over :class:`SessionStore` and an optional :class:`Agent`.

    Wave E responsibilities only:

    * Own a :class:`SessionStore`.
    * Expose :meth:`fork` that produces another :class:`Session` backed
      by a freshly forked store (copy-on-fork via
      :meth:`SessionStore.fork`).

    The :attr:`agent` attribute is optional — a Session with no agent
    is a pure data handle, useful for tests and HTTP fork endpoints
    where the caller does not run the forked session immediately.
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

    # ─── Fork ───────────────────────────────────────────────────────

    def _pending_job_ids(self) -> set[str]:
        """Collect in-flight call ids from the attached agent, if any.

        Used by :meth:`fork` to decide whether the fork point is
        stable. With no agent attached (typical HTTP / resume case),
        there are no in-flight calls so we return an empty set.
        """
        agent = self._agent
        if agent is None:
            return set()

        pending: set[str] = set()
        executor = getattr(agent, "executor", None)
        if executor is None:
            return pending

        # Duck-type: anything exposing a list of pending jobs with a
        # ``call_id`` / ``job_id`` attribute is accepted. Keep the
        # surface small so we don't accidentally freeze the executor
        # interface.
        list_fn = getattr(executor, "list_pending_jobs", None)
        jobs: list[Any]
        if callable(list_fn):
            try:
                jobs = list(list_fn())
            except Exception as e:
                logger.debug(
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

    # ─── Attach (Wave F) ────────────────────────────────────────────

    def attach_agent(self, agent: "Agent", role: str) -> None:
        """Attach ``agent`` to this session under ``role``.

        Thin mirror of :meth:`Agent.attach_to_session` — both call the
        same underlying primitive in :mod:`kohakuterrarium.session.attach`.
        Explicit-only; one session per agent
        (see :class:`kohakuterrarium.session.errors.AlreadyAttachedError`).
        """
        # Lazy import: avoids the Session <-> attach <-> SessionOutput
        # import cycle at module load time.
        from kohakuterrarium.session.attach import attach_agent_to_session

        attach_agent_to_session(agent, self, role)

    def detach_agent(self, agent: "Agent") -> None:
        """Detach ``agent`` from this session.

        Mirror of :meth:`Agent.detach_from_session`. Raises
        :class:`kohakuterrarium.session.errors.NotAttachedError` when
        ``agent`` is not currently attached here.
        """
        # Lazy import matches the ``attach_agent`` pattern above.
        from kohakuterrarium.session.attach import (
            detach_agent_from_session,
            get_attach_state,
        )
        from kohakuterrarium.session.errors import NotAttachedError

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

        The fork runs under a lock to avoid two concurrent forks
        racing to register under the parent's ``forked_children``
        list. The underlying :meth:`SessionStore.fork` is synchronous;
        we off-thread it so the event loop stays responsive even when
        the event log is large.
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
