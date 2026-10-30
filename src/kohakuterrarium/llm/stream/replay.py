"""Keep a retried text stream from repeating text the caller already received."""

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ReplayFilter:
    """Drop the part of a retried stream that repeats already delivered text.

    A retry restarts the reply from the beginning. While the retry reproduces
    the delivered text chunk for chunk, it is suppressed; from the first chunk
    that differs, or once the delivered text is used up, everything passes on.
    """

    def __init__(self) -> None:
        self._delivered = ""
        self._matched = 0
        self._replaying = False

    def begin_attempt(self) -> None:
        """Start an attempt; a retry compares against everything delivered."""
        self._matched = 0
        self._replaying = bool(self._delivered)

    def feed(self, chunk: str) -> str:
        """Return the part of ``chunk`` the caller has not received yet."""
        if not chunk:
            return ""
        if not self._replaying:
            self._delivered += chunk
            return chunk
        remaining = self._delivered[self._matched :]
        overlap = min(len(chunk), len(remaining))
        if chunk[:overlap] != remaining[:overlap]:
            logger.warning(
                "Retried stream diverged from the delivered text; passing it on in full",
                delivered_chars=len(self._delivered),
                matched_chars=self._matched,
            )
            self._replaying = False
            self._delivered += chunk
            return chunk
        self._matched += overlap
        rest = chunk[overlap:]
        if rest:
            self._replaying = False
            self._delivered += rest
        return rest
