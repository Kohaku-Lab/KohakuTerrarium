"""Monotonic stale-home fencing tokens for multi-node Drive routing."""

import itertools
from collections.abc import Iterator

from kohakuterrarium.terrarium.drive.errors import DriveConflictError


def monotonic_token_counter() -> Iterator[int]:
    """Return a deterministic monotonic token source."""
    return itertools.count(1)


class FencingRegistry:
    """Issue and validate monotonic fencing tokens per routing key."""

    def __init__(self, *, counter: Iterator[int] | None = None) -> None:
        self._counter = counter if counter is not None else monotonic_token_counter()
        self._tokens: dict[str, int] = {}

    def issue(self, key: str) -> int:
        """Issue a new current fencing token for ``key``."""
        token = next(self._counter)
        self._tokens[key] = token
        return token

    def current(self, key: str) -> int | None:
        return self._tokens.get(key)

    def is_current(self, key: str, token: int) -> bool:
        return self._tokens.get(key) == token

    def validate(self, key: str, token: int) -> None:
        """Raise when ``token`` is not the live token for ``key``."""
        live = self._tokens.get(key)
        if live != token:
            raise DriveConflictError(
                f"fencing token {token!r} for {key!r} is stale "
                f"(live token is {live!r}); the graph home has moved",
                expected_revision=live,
                actual_revision=token,
            )


__all__ = ["FencingRegistry", "monotonic_token_counter"]
