"""Unit-tier conftest.

Unit tests exercise ONE module against its real dependencies.  Where
a dependency is non-deterministic (LLM, network, clock), use a fake
from ``tests/unit/_fakes/``.  Don't reach for ``MagicMock`` — its
attribute auto-creation hides typos that should fail loudly.
"""

import time

import pytest


class _FrozenClock:
    """Lockstep wall + monotonic clock.

    Use the ``fixed_clock`` fixture to acquire one; call
    ``clock.advance(seconds)`` to move both halves forward.
    """

    def __init__(self) -> None:
        self.t: float = 1_700_000_000.0
        self.mono: float = 1000.0

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.t += seconds
        self.mono += seconds


@pytest.fixture
def fixed_clock(monkeypatch) -> _FrozenClock:
    """Freeze time.monotonic + time.time at deterministic values."""
    c = _FrozenClock()
    monkeypatch.setattr(time, "time", c.time)
    monkeypatch.setattr(time, "monotonic", c.monotonic)
    return c
