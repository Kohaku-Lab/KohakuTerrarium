"""Effect annotations for composition cost analysis.

Each Runnable can optionally carry an Effects annotation describing
expected cost, latency, and reliability.  Combinators compose these
automatically via semiring rules.
"""

from dataclasses import dataclass


@dataclass
class Effects:
    """Optional cost/latency/reliability annotation on a Runnable."""

    cost: float | None = None
    latency: float | None = None
    reliability: float | None = None

    def sequential(self, other: "Effects") -> "Effects":
        """Compose effects for ``f >> g``."""
        return Effects(
            cost=_add(self.cost, other.cost),
            latency=_add(self.latency, other.latency),
            reliability=_mul(self.reliability, other.reliability),
        )

    def parallel(self, other: "Effects") -> "Effects":
        """Compose effects for ``f & g``."""
        return Effects(
            cost=_add(self.cost, other.cost),
            latency=_max(self.latency, other.latency),
            reliability=_mul(self.reliability, other.reliability),
        )


def _add(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a + b


def _max(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return max(a, b)


def _mul(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a * b
