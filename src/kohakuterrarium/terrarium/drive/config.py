"""Define validated configuration objects for the Drive runtime.

Terrarium receives a :class:`DriveRuntimeConfig`, concrete registration
instances, and an optional store through dependency injection. Values are frozen
and validated during construction so invalid settings fail before Drives start.
This leaf module intentionally does not depend on Studio or recipe loading.
"""

from dataclasses import dataclass, field
from typing import Literal

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration
from kohakuterrarium.terrarium.drive.registration import (
    DriveRegistration,
    GenericDriveRegistration,
)

_PERSISTENCE_MODES = frozenset({"auto", "persistent", "ephemeral"})


def _require_int(value: object, name: str, *, minimum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DriveValidationError(f"{name} must be an int, got {value!r}")
    if value < minimum:
        raise DriveValidationError(f"{name} must be >= {minimum}, got {value}")


def _require_number(value: object, name: str, *, minimum: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DriveValidationError(f"{name} must be a number, got {value!r}")
    if value < minimum:
        raise DriveValidationError(f"{name} must be >= {minimum}, got {value}")


@dataclass(frozen=True)
class DriveRetryConfig:
    """Configure delivery retry attempts, backoff, and jitter."""

    max_attempts: int = 5
    initial_backoff_s: float = 2.0
    max_backoff_s: float = 300.0
    jitter: float = 0.1

    def __post_init__(self) -> None:
        _require_int(self.max_attempts, "max_attempts", minimum=1)
        _require_number(self.initial_backoff_s, "initial_backoff_s", minimum=0.0)
        _require_number(self.max_backoff_s, "max_backoff_s", minimum=0.0)
        if self.max_backoff_s < self.initial_backoff_s:
            raise DriveValidationError("max_backoff_s must be >= initial_backoff_s")
        _require_number(self.jitter, "jitter", minimum=0.0)
        if self.jitter > 1.0:
            raise DriveValidationError("jitter must be within [0, 1]")


@dataclass(frozen=True)
class DriveRetentionConfig:
    """Configure retention windows; zero days means immediately eligible."""

    terminal_days: int = 90
    acknowledged_delivery_days: int = 30
    superseded_delivery_days: int = 7
    dead_letter_days: int = 90
    progress_max_count: int = 500
    progress_max_age_days: int = 90

    def __post_init__(self) -> None:
        _require_int(self.terminal_days, "terminal_days", minimum=0)
        _require_int(
            self.acknowledged_delivery_days, "acknowledged_delivery_days", minimum=0
        )
        _require_int(
            self.superseded_delivery_days, "superseded_delivery_days", minimum=0
        )
        _require_int(self.dead_letter_days, "dead_letter_days", minimum=0)
        _require_int(self.progress_max_count, "progress_max_count", minimum=1)
        _require_int(self.progress_max_age_days, "progress_max_age_days", minimum=0)


@dataclass(frozen=True)
class DriveRuntimeConfig:
    """Configure Drive runtime capacity, delivery, retention, and payload limits."""

    enabled: bool = True
    max_active_per_creature: int = 8
    max_pending_per_graph: int = 100
    max_consecutive_drive_turns: int = 3
    dispatcher_concurrency: int = 4
    # A zero cooldown allows immediate readiness re-arming while fairness still
    # limits consecutive Drive turns.
    readiness_cooldown_s: float = 0.0
    retry: DriveRetryConfig = field(default_factory=DriveRetryConfig)
    retention: DriveRetentionConfig = field(default_factory=DriveRetentionConfig)
    spec_max_bytes: int = 16384
    presentation_max_bytes: int = 8192
    metadata_max_bytes: int = 4096
    evidence_max_bytes: int = 16384

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise DriveValidationError("enabled must be a bool")
        _require_int(self.max_active_per_creature, "max_active_per_creature", minimum=1)
        _require_int(self.max_pending_per_graph, "max_pending_per_graph", minimum=1)
        _require_int(
            self.max_consecutive_drive_turns, "max_consecutive_drive_turns", minimum=1
        )
        _require_int(self.dispatcher_concurrency, "dispatcher_concurrency", minimum=1)
        _require_number(self.readiness_cooldown_s, "readiness_cooldown_s", minimum=0.0)
        if not isinstance(self.retry, DriveRetryConfig):
            raise DriveValidationError("retry must be a DriveRetryConfig")
        if not isinstance(self.retention, DriveRetentionConfig):
            raise DriveValidationError("retention must be a DriveRetentionConfig")
        _require_int(self.spec_max_bytes, "spec_max_bytes", minimum=1)
        _require_int(self.presentation_max_bytes, "presentation_max_bytes", minimum=1)
        _require_int(self.metadata_max_bytes, "metadata_max_bytes", minimum=1)
        _require_int(self.evidence_max_bytes, "evidence_max_bytes", minimum=1)


def default_registrations() -> list[DriveRegistration]:
    """Return fresh instances of the default generic and goal registrations."""
    return [GenericDriveRegistration(), GoalDriveRegistration()]


def validate_runtime_selection(
    config: DriveRuntimeConfig,
    registrations: "list[DriveRegistration] | tuple[DriveRegistration, ...]",
) -> None:
    """Reject an enabled runtime that has no Drive registrations."""
    if config.enabled and not registrations:
        raise DriveValidationError(
            "drive runtime is enabled but no registrations were supplied "
            "(design §8.3); omit drive_registrations to use the default generic "
            "and goal registrations"
        )


@dataclass(frozen=True)
class DriveRuntimeSpec:
    """Group resolved, process-local Drive runtime dependencies and provenance."""

    config: DriveRuntimeConfig
    registrations: tuple[DriveRegistration, ...] = ()
    store: object | None = None
    persistence: Literal["auto", "persistent", "ephemeral"] = "auto"
    source_revision: str | None = None
    target_node: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.config, DriveRuntimeConfig):
            raise DriveValidationError("config must be a DriveRuntimeConfig")
        if not isinstance(self.registrations, tuple):
            raise DriveValidationError("registrations must be a tuple")
        if self.persistence not in _PERSISTENCE_MODES:
            raise DriveValidationError(
                f"persistence must be one of {sorted(_PERSISTENCE_MODES)}"
            )
        validate_runtime_selection(self.config, self.registrations)


__all__ = [
    "DriveRetentionConfig",
    "DriveRetryConfig",
    "DriveRuntimeConfig",
    "DriveRuntimeSpec",
    "default_registrations",
    "validate_runtime_selection",
]
