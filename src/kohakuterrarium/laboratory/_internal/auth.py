"""Shared-token authentication for the Laboratory layer.

A single cluster-wide token authenticates clients to the host. The host
holds the expected token; each client presents the same token in its
Hello envelope. Tokens are compared in constant time
(:func:`hmac.compare_digest`) so wrong-token rejection does not leak
timing information about prefix matches.

Empty server token (``""``) disables auth entirely — clients are accepted
without any check. Only safe for in-process tests and local development.

This is the only auth mechanism shipped in 1.5.0.
"""

import hmac

from kohakuterrarium.laboratory._internal.protocol import HelloPayload


class TokenAuth:
    """Validate client-provided tokens against an expected value.

    Attributes:
        is_disabled: ``True`` when the expected token is empty. In this
            mode every client is accepted unconditionally.
    """

    def __init__(self, expected_token: str) -> None:
        if not isinstance(expected_token, str):
            raise TypeError(
                f"expected_token must be a string, "
                f"got {type(expected_token).__name__}"
            )
        self._expected = expected_token

    @property
    def is_disabled(self) -> bool:
        """``True`` if auth is disabled (empty expected token)."""
        return self._expected == ""

    def validate(self, provided_token: str) -> bool:
        """Return whether ``provided_token`` matches the expected token.

        Uses constant-time comparison. When auth is disabled, any input
        (including ``None``) is accepted.
        """
        if self.is_disabled:
            return True
        if not isinstance(provided_token, str):
            return False
        return hmac.compare_digest(
            provided_token.encode("utf-8"),
            self._expected.encode("utf-8"),
        )

    def validate_hello(self, hello: HelloPayload) -> bool:
        """Convenience wrapper: validate the token field of a HelloPayload."""
        return self.validate(hello.token)


__all__ = ["TokenAuth"]
