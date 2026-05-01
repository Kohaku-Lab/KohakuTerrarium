"""Sandbox violation errors."""

from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.modules.sandbox.profile import SandboxProfile


@dataclass(slots=True)
class ProfileViolation(Exception):
    """Raised when an operation exceeds its effective sandbox profile."""

    axis: str
    operation: str
    requested: str
    profile: SandboxProfile
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message:
            self.message = (
                f"Sandbox blocked {self.operation!r} on {self.axis}: "
                f"{self.requested} exceeds profile {self.profile.name}"
            )
        Exception.__init__(self, self.message)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a stable metadata payload."""
        return {
            "type": "profile_violation",
            "axis": self.axis,
            "operation": self.operation,
            "requested": self.requested,
            "message": self.message,
            "profile": self.profile.to_dict(),
            "metadata": dict(self.metadata),
        }
