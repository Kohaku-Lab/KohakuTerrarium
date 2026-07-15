"""Monotonic stale-home fencing tokens for multi-node Drive routing."""

import itertools
from collections.abc import Iterator

from kohakuterrarium.terrarium.drive.errors import DriveConflictError


def monotonic_token_counter() -> Iterator[int]:
    """Return a deterministic process-local source of increasing tokens."""
    return itertools.count(1)


class FencingRegistry:
    """Fence stale graph-home writers with monotonic tokens per routing key."""

    def __init__(self, *, counter: Iterator[int] | None = None) -> None:
        self._counter = counter if counter is not None else monotonic_token_counter()
        self._tokens: dict[str, int] = {}

    def issue(self, key: str) -> int:
        """Advance and return the current token for a routing key."""
        token = next(self._counter)
        self._tokens[key] = token
        return token

    def current(self, key: str) -> int | None:
        return self._tokens.get(key)

    def is_current(self, key: str, token: int) -> bool:
        return self._tokens.get(key) == token

    def validate(self, key: str, token: int) -> None:
        """Reject a token issued before the routing key's latest owner."""
        live = self._tokens.get(key)
        if live != token:
            raise DriveConflictError(
                f"fencing token {token!r} for {key!r} is stale "
                f"(live token is {live!r}); the graph home has moved",
                expected_revision=live,
                actual_revision=token,
            )


__all__ = ["FencingRegistry", "monotonic_token_counter"]
