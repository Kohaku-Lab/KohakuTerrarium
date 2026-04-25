"""Session-level exceptions.

Kept in a small dedicated module so both synchronous :mod:`session.store`
helpers and the async :mod:`session.session` wrapper can import the same
exception types without introducing a cycle.
"""


class ForkNotStableError(Exception):
    """Raised when :meth:`SessionStore.fork` cannot safely fork at a point.

    A fork is "unstable" when copying would leave an in-flight tool or
    sub-agent call *without* its matching result inside the copied
    event range AND the originating :class:`Session` is still running
    that call. The caller is expected to wait for the job to finish or
    cancel it explicitly before retrying the fork.
    """


class AlreadyAttachedError(Exception):
    """Raised when an Agent is already attached to a (different) Session.

    Wave F §2.3 locked decision Q3: one session per agent. Attaching an
    already-attached agent to a *different* session is a usage bug —
    callers should :meth:`Agent.detach_from_session` first. Re-attaching
    to the same session is idempotent and does not raise.
    """


class NotAttachedError(Exception):
    """Raised when :meth:`Agent.detach_from_session` is called on an
    agent that is not currently attached via the Wave F attach API.

    Plain ``attach_session_store`` calls (pre-Wave-F internal) are not
    considered "attached" for the purposes of this check — detach is a
    no-op in that case, not an error.
    """
