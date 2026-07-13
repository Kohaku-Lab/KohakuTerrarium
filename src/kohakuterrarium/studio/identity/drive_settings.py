"""Studio-owned Drive runtime settings — the canonical ``drive-settings.yaml``.

Studio is the management owner (design §2.4, §8.4): it loads/validates the
per-node settings file, joins it with the registration catalog, and resolves an
explicit :class:`DriveRuntimeSpec` that managed engine-construction paths inject
into ``Terrarium(...)``. The low-level engine never reads this file.

Locked semantics (design §8.4):

- canonical file is ``config_dir() / "drive-settings.yaml"`` (honours
  ``KT_CONFIG_DIR``); it is NOT the launcher's ``app-settings.json``;
- the file stores serializable selections/options only — never live Python
  objects or Drive records;
- an absent file is atomically initialized with the runtime and built-in generic
  and goal registrations enabled; a malformed file raises a typed validation
  error and the last valid file is left untouched;
- writes are atomic (tmp + replace) and optimistic-concurrency protected by a
  content-hash ``revision``; a stale write raises :class:`DriveSettingsConflictError`;
- **save and apply are distinct**: :func:`save_settings` only persists validated
  config; :func:`apply_runtime` is the separate typed live-application operation
  returning ``applied_live`` / ``restart_required`` / ``rejected``.
"""

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from kohakuterrarium.studio.catalog.drive_registrations import (
    instantiate_registration,
    list_drive_registrations_status,
)
from kohakuterrarium.studio.identity.drive_settings_io import (
    _SAVE_LOCK,
    _acquire_save_lock,
    _atomic_write,
    _check_expected_revision,
)
from kohakuterrarium.terrarium.drive.config import (
    DriveRetentionConfig,
    DriveRetryConfig,
    DriveRuntimeConfig,
    DriveRuntimeSpec,
)
from kohakuterrarium.terrarium.drive.errors import (
    DriveError,
    DriveSettingsConflictError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.registration import effective_options
from kohakuterrarium.terrarium.drive.registration_options import (
    implementation_fingerprint,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Reconfigure result strings mirror ``drive.runtime`` (design §8.6) so callers
# depend on one vocabulary. Imported lazily-free here to avoid an engine import.
APPLIED_LIVE = "applied_live"
RESTART_REQUIRED = "restart_required"
REJECTED = "rejected"

CURRENT_SCHEMA_VERSION = 1
DEFAULT_NODE = "_host"

_RUNTIME_INT_FIELDS = (
    "max_active_per_creature",
    "max_pending_per_graph",
    "max_consecutive_drive_turns",
    "dispatcher_concurrency",
    "spec_max_bytes",
    "presentation_max_bytes",
    "metadata_max_bytes",
    "evidence_max_bytes",
)


def drive_settings_path() -> Path:
    """The ``drive-settings.yaml`` path, honouring ``KT_CONFIG_DIR``.

    Resolved fresh per call so test isolation / operator re-homing works.
    """
    return config_dir() / "drive-settings.yaml"


@dataclass(frozen=True)
class RegistrationSetting:
    """Per-registration selection: whether it is enabled and its options."""

    enabled: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriveSettings:
    """Parsed + validated ``drive-settings.yaml`` (design §8.4).

    ``runtime`` reuses :class:`DriveRuntimeConfig` (its ``__post_init__`` is the
    single source of tuning validation). ``revision`` is the content hash of the
    on-disk bytes this was loaded from / would be saved as; it is excluded from
    equality so two settings with identical content compare equal.
    """

    runtime: DriveRuntimeConfig = field(default_factory=DriveRuntimeConfig)
    registrations: dict[str, RegistrationSetting] = field(default_factory=dict)
    schema_version: int = CURRENT_SCHEMA_VERSION
    revision: str | None = field(default=None, compare=False)

    def enabled_registration_names(self) -> list[str]:
        return sorted(n for n, r in self.registrations.items() if r.enabled)


def default_settings() -> DriveSettings:
    """Default-on runtime with the built-in generic and goal kinds enabled."""
    return DriveSettings(
        registrations={
            "generic": RegistrationSetting(enabled=True),
            "goal": RegistrationSetting(enabled=True),
        }
    )


class SaveDurability(Enum):
    """Crash-durability a save actually achieved (design §8.4).

    ``FULL`` = file contents AND the directory-entry rename were fsync-durable;
    ``FILE_ONLY`` = only file *contents* are guaranteed (the directory-fsync
    barrier was unavailable — Windows has no directory-fsync API, or the
    filesystem rejected it with ``EINVAL``)."""

    FULL = "full"
    FILE_ONLY = "file_only"


@dataclass(frozen=True)
class SaveSettingsResult:
    """Saved settings and the crash durability actually achieved."""

    settings: DriveSettings
    durability: SaveDurability = SaveDurability.FULL

    @property
    def revision(self) -> str | None:
        """The saved revision, retained for compatibility with DriveSettings."""
        return self.settings.revision


def _require_dict(value: object, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DriveValidationError(
            f"{name} must be a mapping, got {type(value).__name__}"
        )
    return value


def _parse_runtime(raw: dict[str, Any]) -> DriveRuntimeConfig:
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise DriveValidationError("runtime.enabled must be a boolean")
    kwargs: dict[str, Any] = {"enabled": enabled}
    for name in _RUNTIME_INT_FIELDS:
        if name in raw:
            kwargs[name] = raw[name]
    retry_raw = _require_dict(raw.get("retry"), "runtime.retry")
    retention_raw = _require_dict(raw.get("retention"), "runtime.retention")
    # DriveRetryConfig / DriveRetentionConfig / DriveRuntimeConfig each validate
    # their own fields in __post_init__ -> malformed values raise DriveValidationError.
    if retry_raw:
        kwargs["retry"] = DriveRetryConfig(
            **_filter_dataclass(DriveRetryConfig, retry_raw)
        )
    if retention_raw:
        kwargs["retention"] = DriveRetentionConfig(
            **_filter_dataclass(DriveRetentionConfig, retention_raw)
        )
    return DriveRuntimeConfig(**kwargs)


def _filter_dataclass(cls: type, raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only the keys ``cls`` declares — unknown keys are ignored for
    forward-compatibility; the values are validated by ``cls.__post_init__``."""
    fields = {f for f in cls.__dataclass_fields__}
    return {k: v for k, v in raw.items() if k in fields}


def _parse_registrations(raw: dict[str, Any]) -> dict[str, RegistrationSetting]:
    out: dict[str, RegistrationSetting] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise DriveValidationError(
                f"registration name must be a non-empty string, got {name!r}"
            )
        entry = _require_dict(entry, f"registrations.{name}")
        enabled = entry.get("enabled", False)
        if not isinstance(enabled, bool):
            raise DriveValidationError(
                f"registrations.{name}.enabled must be a boolean"
            )
        options = _require_dict(entry.get("options"), f"registrations.{name}.options")
        out[name] = RegistrationSetting(enabled=enabled, options=dict(options))
    return out


def parse_settings(raw: object, *, revision: str | None = None) -> DriveSettings:
    """Validate a raw mapping into :class:`DriveSettings` (design §8.4).

    Raises :class:`DriveValidationError` on any structural problem so a malformed
    file is surfaced rather than silently enabling code.
    """
    raw = _require_dict(raw, "drive-settings")
    schema_version = raw.get("schema_version", CURRENT_SCHEMA_VERSION)
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise DriveValidationError("schema_version must be an int")
    if schema_version > CURRENT_SCHEMA_VERSION:
        raise DriveValidationError(
            f"drive-settings schema_version {schema_version} is newer than supported "
            f"({CURRENT_SCHEMA_VERSION}); upgrade KohakuTerrarium"
        )
    runtime = _parse_runtime(_require_dict(raw.get("runtime"), "runtime"))
    registrations = (
        default_settings().registrations
        if "registrations" not in raw
        else _parse_registrations(_require_dict(raw["registrations"], "registrations"))
    )
    return DriveSettings(
        runtime=runtime,
        registrations=registrations,
        schema_version=schema_version,
        revision=revision,
    )


def settings_to_dict(settings: DriveSettings) -> dict[str, Any]:
    """Canonical serializable mapping (stable key order for a stable revision)."""
    runtime = settings.runtime
    return {
        "schema_version": settings.schema_version,
        "runtime": {
            "enabled": runtime.enabled,
            "max_active_per_creature": runtime.max_active_per_creature,
            "max_pending_per_graph": runtime.max_pending_per_graph,
            "max_consecutive_drive_turns": runtime.max_consecutive_drive_turns,
            "dispatcher_concurrency": runtime.dispatcher_concurrency,
            "spec_max_bytes": runtime.spec_max_bytes,
            "presentation_max_bytes": runtime.presentation_max_bytes,
            "metadata_max_bytes": runtime.metadata_max_bytes,
            "evidence_max_bytes": runtime.evidence_max_bytes,
            "retry": {
                "max_attempts": runtime.retry.max_attempts,
                "initial_backoff_s": runtime.retry.initial_backoff_s,
                "max_backoff_s": runtime.retry.max_backoff_s,
                "jitter": runtime.retry.jitter,
            },
            "retention": {
                "terminal_days": runtime.retention.terminal_days,
                "acknowledged_delivery_days": runtime.retention.acknowledged_delivery_days,
                "superseded_delivery_days": runtime.retention.superseded_delivery_days,
                "dead_letter_days": runtime.retention.dead_letter_days,
                "progress_max_count": runtime.retention.progress_max_count,
                "progress_max_age_days": runtime.retention.progress_max_age_days,
            },
        },
        "registrations": {
            name: {"enabled": rs.enabled, "options": rs.options}
            for name, rs in sorted(settings.registrations.items())
        },
    }


def _serialize(settings: DriveSettings) -> bytes:
    text = yaml.safe_dump(
        settings_to_dict(settings), default_flow_style=False, sort_keys=False
    )
    return text.encode("utf-8")


def _revision_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_settings() -> DriveSettings:
    """Load + validate the settings file (design §8.4).

    An absent file is initialized atomically to :func:`default_settings`, then
    loaded normally so callers always receive its content-hash revision. A
    malformed file raises :class:`DriveValidationError` and is left untouched.
    """
    path = drive_settings_path()
    if not path.exists():
        try:
            save_settings(default_settings(), expected_exists=False)
        except DriveSettingsConflictError:
            # A conflict means another process won only if its file now exists;
            # lock timeouts without a winner must remain visible to the caller.
            if not path.exists():
                raise
    data = path.read_bytes()
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise DriveValidationError(
            f"drive-settings.yaml is not valid YAML: {exc}"
        ) from exc
    return parse_settings(raw, revision=_revision_of(data))


def current_revision() -> str | None:
    """The on-disk revision (content hash), or ``None`` when no file exists."""
    path = drive_settings_path()
    if not path.exists():
        return None
    return _revision_of(path.read_bytes())


def save_settings(
    settings: DriveSettings | dict[str, Any],
    *,
    expected_revision: str | None = None,
    expected_exists: bool | None = None,
) -> SaveSettingsResult:
    """Validate + atomically persist settings (design §8.4).

    ``settings`` may be a :class:`DriveSettings` or a raw mapping (validated
    first). ``expected_exists=False`` explicitly means expect-absent;
    ``expected_exists=None`` and no revision preserves unconditional-write
    compatibility. A supplied revision must exactly match an existing file.
    Returns the saved settings plus the :class:`SaveDurability` achieved: a
    ``FILE_ONLY`` result means only file contents are crash-durable because the
    directory-entry fsync barrier was unavailable.
    """
    parsed = (
        settings if isinstance(settings, DriveSettings) else parse_settings(settings)
    )
    path = drive_settings_path()
    new_bytes = _serialize(parsed)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Hold BOTH locks across the optimistic check AND the replace so the read and
    # write are one atomic critical section. ``_SAVE_LOCK`` excludes in-process
    # threads; the OS file lock excludes separate processes. Either way the loser
    # reads the winner's fresh revision and conflicts instead of silently
    # overwriting it (R1-28).
    with _SAVE_LOCK:
        file_lock = _acquire_save_lock(path)
        try:
            _check_expected_revision(
                path,
                expected_revision=expected_revision,
                expected_exists=expected_exists,
            )
            dir_barrier = _atomic_write(path, new_bytes)
        finally:
            file_lock.release()
    durability = SaveDurability.FULL if dir_barrier else SaveDurability.FILE_ONLY
    if durability is SaveDurability.FILE_ONLY:
        logger.warning(
            "drive-settings.yaml saved with FILE_ONLY durability: file contents "
            "are crash-durable but the directory-entry rename barrier was "
            "unavailable; downstream surfaces should propagate this",
            durability=durability.value,
            path=str(path),
        )
    return SaveSettingsResult(
        settings=replace(parsed, revision=_revision_of(new_bytes)),
        durability=durability,
    )


# ---------------------------------------------------------------------------
# resolve — settings + catalog -> explicit DriveRuntimeSpec
# ---------------------------------------------------------------------------


def resolve_runtime(node: str = DEFAULT_NODE) -> DriveRuntimeSpec:
    """Resolve settings into an explicit :class:`DriveRuntimeSpec` (design §8.4).

    Imports/instantiates ONLY enabled registrations. A missing/unloadable enabled
    registration raises its typed error (never a silent substitution); enabling
    the runtime with no enabled registration is rejected by
    :class:`DriveRuntimeSpec` validation. When the runtime is disabled the catalog
    is not scanned at all.
    """
    settings = load_settings()
    if not settings.runtime.enabled:
        return DriveRuntimeSpec(
            config=settings.runtime,
            registrations=(),
            source_revision=settings.revision,
            target_node=node,
        )
    registrations = tuple(
        instantiate_registration(name, settings.registrations[name].options)
        for name in settings.enabled_registration_names()
    )
    # Surface duplicate-name / kind-collision loudly at resolve time (the same
    # check the engine runs when it builds its snapshot).
    EnabledRegistrySnapshot.build(registrations)
    return DriveRuntimeSpec(
        config=settings.runtime,
        registrations=registrations,
        source_revision=settings.revision,
        target_node=node,
    )


def resolve_drive_kwargs(
    node: str = DEFAULT_NODE, *, strict: bool = False
) -> dict[str, Any]:
    """Explicit ``Terrarium(...)`` Drive kwargs from managed settings.

    Managed construction paths call this exactly once and splat the result into
    the constructor. ``strict=False`` (the construction default) degrades a
    settings/registration error to a **disabled** runtime with a logged warning
    so one bad setting cannot take the whole process down; ``strict=True`` (the
    Settings save/apply path) re-raises so the operator sees the error.
    """
    try:
        spec = resolve_runtime(node)
    except DriveError as exc:
        if strict:
            raise
        logger.warning(
            "drive settings resolution failed; runtime disabled", error=str(exc)
        )
        return {
            "drive_config": DriveRuntimeConfig(enabled=False),
            "drive_registrations": (),
            "drive_store": None,
        }
    return {
        "drive_config": spec.config,
        "drive_registrations": spec.registrations,
        "drive_store": spec.store,
    }


# ---------------------------------------------------------------------------
# apply — the distinct live-application operation (design §8.6)
# ---------------------------------------------------------------------------


def _config_tuning_differs(a: DriveRuntimeConfig, b: DriveRuntimeConfig) -> bool:
    """Whether two runtime configs differ in anything other than ``enabled``."""
    return replace(a, enabled=False) != replace(b, enabled=False)


def _engine_runtime_revision(engine: Any) -> str | None:
    """Opaque hash of the engine's *running* Drive runtime state (or ``None``).

    Hashes each enabled registration's effective *identity* — name, kind, schema
    range, provenance package, compatibility, verifier mode, and the resolved
    implementation fingerprint (module + qualname + distribution version) — plus
    its normalized effective options and the runtime tuning (R1-29). A same-name
    implementation-class / schema / package / option change therefore moves the
    running revision instead of being silently invisible.
    """
    drives = getattr(engine, "drives", None)
    if drives is None:
        return None
    entries = []
    for e in sorted(drives.snapshot.entries, key=lambda e: e.descriptor.name):
        d = e.descriptor
        entries.append(
            {
                "name": d.name,
                "kind": d.kind,
                "schema_version": d.schema_version,
                "min_schema_version": d.min_version,
                "source_package": d.source_package,
                "compatibility": d.compatibility,
                "verifier_mode": d.verifier_mode,
                "options": effective_options(e.registration),
                "impl": implementation_fingerprint(e.registration),
            }
        )
    payload = json.dumps(
        {
            "entries": entries,
            "runtime": settings_to_dict(DriveSettings(runtime=drives.config))[
                "runtime"
            ],
        },
        sort_keys=True,
        default=str,
    )
    return _revision_of(payload.encode("utf-8"))


def apply_runtime(engine: Any, *, node: str = DEFAULT_NODE) -> dict[str, Any]:
    """Apply the current settings to a live engine (design §8.6).

    Returns ``{result, desired_revision, running_revision, warnings}`` where
    ``result`` is ``applied_live`` / ``restart_required`` / ``rejected``. Only a
    live registration-set change on an already-enabled runtime with unchanged
    tuning is applied live; turning the runtime on/off or changing tuning
    conservatively reports ``restart_required`` (v1 boundary). A save never
    implies apply.
    """
    running_rev = _engine_runtime_revision(engine)
    try:
        spec = resolve_runtime(node)
    except DriveError as exc:
        return {
            "result": REJECTED,
            "desired_revision": None,
            "running_revision": running_rev,
            "warnings": [str(exc)],
        }
    desired_rev = spec.source_revision
    warnings: list[str] = []
    drives = getattr(engine, "drives", None)
    if drives is None:
        if spec.config.enabled:
            warnings.append("enabling the Drive runtime requires an engine restart")
            result = RESTART_REQUIRED
        else:
            result = APPLIED_LIVE
    elif not spec.config.enabled:
        warnings.append("disabling the Drive runtime requires an engine restart")
        result = RESTART_REQUIRED
    elif _config_tuning_differs(drives.config, spec.config):
        warnings.append("runtime tuning changes require an engine restart")
        result = RESTART_REQUIRED
    else:
        result = engine.reconfigure_drives(spec.registrations)
        if result == RESTART_REQUIRED:
            warnings.append("registration removal requires an engine restart")
    return {
        "result": result,
        "desired_revision": desired_rev,
        "running_revision": _engine_runtime_revision(engine),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# status DTO for the Settings panel
# ---------------------------------------------------------------------------


def settings_status(node: str = DEFAULT_NODE) -> dict[str, Any]:
    """Settings-file view for the Settings panel (available/enabled/load status).

    Never raises on a malformed file — the parse error is surfaced as
    ``parse_error`` so the panel can render it instead of 500-ing.
    """
    try:
        settings = load_settings()
    except DriveValidationError as exc:
        return {
            "node": node,
            "settings_revision": current_revision(),
            "parse_error": str(exc),
            "runtime": None,
            "registrations": [],
        }
    enabled_names = set(settings.enabled_registration_names())
    options_by_name = {n: settings.registrations[n].options for n in enabled_names}
    return {
        "node": node,
        "settings_revision": settings.revision,
        "parse_error": None,
        "runtime": settings_to_dict(settings)["runtime"],
        "registrations": list_drive_registrations_status(
            enabled_names, options_by_name
        ),
    }


__all__ = [
    "APPLIED_LIVE",
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_NODE",
    "REJECTED",
    "RESTART_REQUIRED",
    "DriveSettings",
    "RegistrationSetting",
    "SaveDurability",
    "SaveSettingsResult",
    "apply_runtime",
    "current_revision",
    "default_settings",
    "drive_settings_path",
    "load_settings",
    "parse_settings",
    "resolve_drive_kwargs",
    "resolve_runtime",
    "save_settings",
    "settings_status",
    "settings_to_dict",
]
