"""Define errors shared by session stores, forks, and attachments."""


class ForkNotStableError(Exception):
    """Raised when :meth:`SessionStore.fork` cannot safely fork at a point.

    An unstable fork would split a tool or sub-agent call from its result while
    that job is still active. Callers must wait or cancel before retrying.
    """


class AlreadyAttachedError(Exception):
    """Raised when an agent is attached to a different session.

    Reattaching to the same session is idempotent; switching sessions requires
    explicit detachment.
    """


class NotAttachedError(Exception):
    """Raised when detachment is requested without a session attachment.

    Direct store attachment does not count as a detachable session binding.
    """
