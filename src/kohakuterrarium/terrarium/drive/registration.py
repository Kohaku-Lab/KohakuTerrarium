"""Drive registration protocol, descriptor, and the builtin generic registration.

A *registration* supplies the deterministic, kind-specific policy Terrarium is
allowed to run for a Drive kind (design §8.1): spec validation, transition
constraints, readiness, event projection, terminal verification, and a bounded
prompt contribution. Registrations are runtime extension objects — **not** Agent
plugins and **not** recipe fields — and may never run an LLM, write the
repository, or dispatch events.

Discovery is separate from enablement (§8.2): a package declares a
``drive_registrations:`` manifest slot and its :class:`DriveRegistrationDescriptor`
is listed *without* importing the implementation module. Code is imported only
through :func:`resolve_registration`, on explicit enable/validate.

The immutable enabled-registry snapshot and derived-availability calculation
live in :mod:`drive.snapshot`, which builds on this module.

Leaf module: imports only stdlib, :mod:`kohakuterrarium.errors`-derived Drive
errors, and the logger. No Agent/engine/LLM/plugin imports.
"""

import importlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from kohakuterrarium.terrarium.drive.errors import (
    DriveError,
    DriveRegistrationIncompatibleError,
    DriveRegistrationNotFoundError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.registration_options import (
    EFFECTIVE_OPTIONS_ATTR,
    apply_registration_options,
    effective_options,
    json_bytes,
    json_type_equal,
    option_type_ok,
    require_json_safe,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Byte budgets for prompt normalization at registration load time (§8.7). A
# per-registration cap bounds one contribution; the aggregate cap bounds the
# sum across all enabled registrations so the system prompt stays small.
PER_REGISTRATION_PROMPT_MAX_BYTES = 2048
AGGREGATE_PROMPT_MAX_BYTES = 8192
# Reference opaque-spec byte cap. The engine's DriveRuntimeConfig carries the
# AUTHORITATIVE per-field limits (§13, R1-41): by default the generic
# registration imposes no independent byte cap so a runtime ``spec_max_bytes``
# larger than this value is fully effective; an explicit cap is honoured only
# when a caller opts into a tighter one.
DEFAULT_SPEC_MAX_BYTES = 16384

# role name -> the method a registration exposes for that role.
ROLE_METHODS: dict[str, str] = {
    "spec": "validate_spec",
    "transition": "validate_transition",
    "readiness": "readiness",
    "projection": "project_event",
    "verifier": "verify_terminal",
    "prompt": "prompt_contribution",
}
ALL_ROLES = frozenset(ROLE_METHODS)
_VERIFIER_MODES = frozenset({"none", "actor", "extension", "two_party"})

# Distinguishes an absent compatibility marker from an explicit ``None`` (R1-18).
_MISSING = object()

_GENERIC_PROMPT = (
    "Generic drives carry an opaque instruction spec. Pursue the instruction, "
    "report material progress with evidence, and propose completion or failure "
    "for verification rather than asserting a terminal outcome yourself."
)


# ---------------------------------------------------------------------------
# Role result types (design §8.1 role split)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Readiness:
    """Deterministic readiness verdict for a Drive (design §4.4).

    ``ready`` is the admission gate: the manager consults it before every
    admission (create / scan / activation), so a registration that returns
    ``ready=False`` earns no delivery there (design §5.2). ``initial`` is the
    explicit escape hatch for the *one* initial event of an activation epoch —
    a manual-mode Goal returns ``ready=False, initial=True`` so ``/goal set``
    still produces a single initial delivery (design §11.4) without looping.
    ``re_arm`` is the separate post-settlement continuation signal: when True
    the manager advances the Drive's readiness generation and enqueues the next
    delivery once the prior one settles (design §4.4 "creates the next readiness
    generation, according to enabled registration policy"). All default False so
    a plain ``Readiness(ready=False)`` is unambiguously "never deliver".
    """

    ready: bool
    reason: str | None = None
    next_check_at: datetime | None = None
    re_arm: bool = False
    initial: bool = False


@dataclass(frozen=True)
class DriveProjection:
    """Bounded event projection a registration produces for delivery (§5.1)."""

    event_type: str
    prompt_override: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a terminal-proposal verifier (design §4.2)."""

    approved: bool
    reason: str | None = None


@runtime_checkable
class DriveRegistration(Protocol):
    """Runtime policy object serving one Drive ``kind`` (design §8.1).

    Concrete registrations may implement any subset of the role methods named
    in :data:`ROLE_METHODS`; whether a role is required is declared by the
    descriptor's ``required_roles``. ``descriptor()`` returns the wire-safe
    metadata used for discovery and prompt normalization.
    """

    name: str
    kind: str
    schema_version: int

    def descriptor(self) -> "DriveRegistrationDescriptor": ...


# ---------------------------------------------------------------------------
# DriveRegistrationDescriptor — wire-safe metadata; carries no live object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriveRegistrationDescriptor:
    """Identity + capability metadata for one registration (design §8.1).

    Every field is a primitive so the descriptor is JSON/msgpack-safe; a
    persisted settings selection identifies a registration by ``name`` +
    options only, never by a pickled object. A descriptor scanned from a
    manifest carries ``module``/``class_name`` but no imported code.
    """

    name: str
    kind: str
    schema_version: int

    min_schema_version: int | None = None
    source_package: str | None = None
    module: str | None = None
    class_name: str | None = None
    description: str = ""
    required_roles: frozenset[str] = frozenset()
    optional_roles: frozenset[str] = frozenset()
    prompt_contribution: str | None = None
    option_defaults: dict[str, Any] = field(default_factory=dict)
    option_schema: dict[str, Any] = field(default_factory=dict)
    # Capability markers the manifest advertises; matched by
    # :meth:`assert_implementation_compatible` (design §8.2, R1-18).
    compatibility: dict[str, Any] = field(default_factory=dict)
    verifier_mode: str = "none"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DriveValidationError(
                f"registration name must be non-empty, got {self.name!r}"
            )
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise DriveValidationError(
                f"registration kind must be non-empty, got {self.kind!r}"
            )
        if not isinstance(self.schema_version, int) or isinstance(
            self.schema_version, bool
        ):
            raise DriveValidationError("schema_version must be an int")
        if self.schema_version < 1:
            raise DriveValidationError("schema_version must be >= 1")
        if self.min_schema_version is not None:
            if not isinstance(self.min_schema_version, int) or isinstance(
                self.min_schema_version, bool
            ):
                raise DriveValidationError("min_schema_version must be an int")
            if not 1 <= self.min_schema_version <= self.schema_version:
                raise DriveValidationError(
                    "min_schema_version must be within [1, schema_version]"
                )
        for role in self.required_roles | self.optional_roles:
            if role not in ALL_ROLES:
                raise DriveValidationError(
                    f"unknown role {role!r}; roles are {sorted(ALL_ROLES)}"
                )
        if (self.module is None) != (self.class_name is None):
            raise DriveValidationError(
                "module and class_name must be supplied together or both omitted"
            )
        if self.verifier_mode not in _VERIFIER_MODES:
            raise DriveValidationError(
                f"verifier_mode must be one of {sorted(_VERIFIER_MODES)}"
            )

    @property
    def min_version(self) -> int:
        """Lowest schema version this registration accepts (default: exact)."""
        return (
            self.min_schema_version
            if self.min_schema_version is not None
            else self.schema_version
        )

    def supports_schema(self, version: int) -> bool:
        return self.min_version <= version <= self.schema_version

    def assert_implementation_compatible(
        self, impl: "DriveRegistrationDescriptor"
    ) -> None:
        """Fail closed when a resolved implementation contradicts the manifest.

        ``self`` is the manifest descriptor, ``impl`` the loaded class's; the impl
        must honour the manifest's advertised identity, schema range, required
        roles, verifier mode, and compatibility markers — every manifest marker
        present + equal in ``impl.compatibility`` (design §8.2, R1-18).
        """
        if impl.name != self.name or impl.kind != self.kind:
            raise DriveValidationError(
                f"registration identity mismatch for {self.name!r}: manifest "
                f"{self.name}/{self.kind} vs class {impl.name}/{impl.kind}"
            )
        if not impl.supports_schema(self.schema_version) or not impl.supports_schema(
            self.min_version
        ):
            raise DriveRegistrationIncompatibleError(
                f"registration {self.name!r}: manifest advertises schema "
                f"[{self.min_version},{self.schema_version}] but the implementation "
                f"supports [{impl.min_version},{impl.schema_version}]"
            )
        missing = self.required_roles - (impl.required_roles | impl.optional_roles)
        if missing:
            raise DriveValidationError(
                f"registration {self.name!r}: manifest requires role(s) "
                f"{sorted(missing)} the implementation does not declare"
            )
        if self.verifier_mode != impl.verifier_mode:
            raise DriveValidationError(
                f"registration {self.name!r}: manifest verifier_mode "
                f"{self.verifier_mode!r} != implementation {impl.verifier_mode!r}"
            )
        for marker, want in self.compatibility.items():
            got = impl.compatibility.get(marker, _MISSING)
            if got is _MISSING or not json_type_equal(want, got):
                shown = "<absent>" if got is _MISSING else repr(got)
                raise DriveRegistrationIncompatibleError(
                    f"registration {self.name!r}: manifest requires compatibility "
                    f"{marker!r}={want!r} but the implementation advertises {shown}"
                )

    def normalized_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        """Merge caller options over the declared defaults (validated JSON-safe)."""
        merged = dict(self.option_defaults)
        if options:
            if not isinstance(options, dict):
                raise DriveValidationError("options must be a dict")
            require_json_safe(options, "options")
            merged.update(options)
        return merged

    def validate_options(self, effective: dict[str, Any]) -> None:
        """Validate resolved options against the declared schema/defaults (R1-17).

        A key that is neither in ``option_schema`` nor ``option_defaults`` is
        unknown and rejected; a key with a schema ``type``/``choices`` must match.
        The declared set is the union of schema + defaults so a registration may
        declare a default without a schema entry."""
        declared = set(self.option_schema) | set(self.option_defaults)
        for key, value in effective.items():
            if key not in declared:
                raise DriveValidationError(
                    f"unknown option {key!r} for registration {self.name!r}"
                )
            spec = self.option_schema.get(key)
            if not isinstance(spec, dict):
                continue
            expected = spec.get("type")
            if expected is not None and not option_type_ok(value, expected):
                raise DriveValidationError(
                    f"option {key!r} for registration {self.name!r} must be of "
                    f"type {expected!r}"
                )
            choices = spec.get("choices")
            if choices is not None and value not in choices:
                raise DriveValidationError(
                    f"option {key!r} for registration {self.name!r} must be one of "
                    f"{list(choices)}"
                )

    def to_dict(self) -> dict[str, Any]:
        """Primitive catalog projection (frozensets become sorted lists)."""
        return {
            "name": self.name,
            "kind": self.kind,
            "schema_version": self.schema_version,
            "min_schema_version": self.min_version,
            "source_package": self.source_package,
            "module": self.module,
            "class_name": self.class_name,
            "description": self.description,
            "required_roles": sorted(self.required_roles),
            "optional_roles": sorted(self.optional_roles),
            "verifier_mode": self.verifier_mode,
            "has_prompt": self.prompt_contribution is not None,
            # Additive Settings-UI fields (design §12.2): schema-driven option
            # form + bounded prompt preview, straight from descriptor metadata.
            "option_schema": dict(self.option_schema),
            "option_defaults": dict(self.option_defaults),
            "prompt_preview": self.prompt_contribution,
        }

    @classmethod
    def from_manifest(
        cls, package: str, entry: dict[str, Any]
    ) -> "DriveRegistrationDescriptor":
        """Build a full descriptor from a ``drive_registrations:`` manifest entry
        WITHOUT importing the implementation module (design §8.2, R1-18).

        Every supported field is parsed + validated here (roles, verifier mode,
        min schema, prompt/option/compatibility metadata); the field validators
        live in :meth:`__post_init__`."""
        if not isinstance(entry, dict):
            raise DriveValidationError("drive_registrations entry must be a mapping")
        name = entry.get("name")
        kind = entry.get("kind")
        module = entry.get("module")
        class_name = entry.get("class") or entry.get("class_name")
        if not name or not kind:
            raise DriveValidationError(
                f"drive_registrations entry in package {package!r} needs 'name' and 'kind'"
            )
        if not module or not class_name:
            raise DriveValidationError(
                f"drive_registrations entry {name!r} in package {package!r} needs "
                "'module' and 'class'"
            )
        schema_version = entry.get("schema_version", 1)
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise DriveValidationError(
                f"schema_version for {name!r} in {package!r} must be an int"
            )
        where = f"{name!r} in {package!r}"
        return cls(
            name=str(name),
            kind=str(kind),
            schema_version=schema_version,
            min_schema_version=_opt_manifest_int(
                entry.get("min_schema_version"), f"min_schema_version for {where}"
            ),
            source_package=package,
            module=str(module),
            class_name=str(class_name),
            description=str(entry.get("description", "")),
            required_roles=_manifest_role_set(
                entry.get("required_roles"), f"required_roles for {where}"
            ),
            optional_roles=_manifest_role_set(
                entry.get("optional_roles"), f"optional_roles for {where}"
            ),
            prompt_contribution=_opt_manifest_str(entry.get("prompt_contribution")),
            option_defaults=_manifest_dict(
                entry.get("option_defaults"), f"option_defaults for {where}"
            ),
            option_schema=_manifest_dict(
                entry.get("option_schema"), f"option_schema for {where}"
            ),
            compatibility=_manifest_dict(
                entry.get("compatibility"), f"compatibility for {where}"
            ),
            verifier_mode=str(entry.get("verifier_mode", "none")),
        )


# ---------------------------------------------------------------------------
# Builtin generic registration (design §8.1)
# ---------------------------------------------------------------------------


class GenericDriveRegistration:
    """Opaque-spec builtin registration with manual terminal proposals.

    Accepts any JSON-safe spec within a byte cap, adds no transition edges
    beyond the generic graph, is trivially ready, and requires no verifier
    (``verifier_mode="none"``). Contributes a minimal bounded prompt.
    """

    name = "generic"
    kind = "generic"
    schema_version = 1
    description = "Opaque-spec drive with manual terminal proposals."

    def __init__(self, *, spec_max_bytes: int | None = None) -> None:
        # ``None`` (default) means no independent byte cap: the manager's central
        # runtime ``spec_max_bytes`` is authoritative, so a larger runtime cap is
        # effective (R1-09). An explicit int imposes a tighter belt-and-suspenders
        # cap on top; JSON safety is always checked.
        if spec_max_bytes is not None and (
            not isinstance(spec_max_bytes, int) or spec_max_bytes < 1
        ):
            raise DriveValidationError("spec_max_bytes must be a positive int")
        self._spec_max_bytes = spec_max_bytes

    def descriptor(self) -> DriveRegistrationDescriptor:
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=self.schema_version,
            description=self.description,
            required_roles=frozenset({"spec", "transition", "readiness"}),
            optional_roles=frozenset({"projection", "verifier", "prompt"}),
            prompt_contribution=_GENERIC_PROMPT,
            verifier_mode="none",
        )

    def validate_spec(self, spec: dict[str, Any]) -> None:
        if not isinstance(spec, dict):
            raise DriveValidationError("generic drive spec must be a dict")
        require_json_safe(spec, "spec")
        # The manager's central per-field validator enforces the authoritative
        # runtime byte cap (R1-09); this only applies an EXPLICIT tighter cap.
        if self._spec_max_bytes is None:
            return
        size = json_bytes(spec)
        if size > self._spec_max_bytes:
            raise DriveValidationError(
                f"generic drive spec is {size} bytes, over the {self._spec_max_bytes}-byte cap"
            )

    def validate_transition(self, before: Any, proposal: Any, context: Any) -> None:
        # Generic edges are enforced by drive_policy; no extra transitions.
        return None

    def readiness(self, drive: Any, dependencies: Any, now: Any) -> Readiness:
        return Readiness(ready=True)

    def project_event(
        self, drive: Any, assignment: Any, reason: Any
    ) -> DriveProjection:
        return DriveProjection(event_type="drive_ready", context={"kind": self.kind})

    def verify_terminal(self, proposal: Any, context: Any) -> VerificationResult:
        return VerificationResult(approved=True)

    def prompt_contribution(self) -> str | None:
        return _GENERIC_PROMPT


def builtin_registrations() -> list[DriveRegistration]:
    """The framework's builtin registrations (currently just ``generic``)."""
    return [GenericDriveRegistration()]


# ---------------------------------------------------------------------------
# Lazy resolution of a manifest-declared registration (import on enable only)
# ---------------------------------------------------------------------------


def resolve_registration(descriptor: DriveRegistrationDescriptor) -> DriveRegistration:
    """Import + instantiate a descriptor's implementation (design §8.2).

    Called only when enabling/validating a registration — never during a
    catalog scan. Every failure is a provenance-carrying typed error.
    """
    if descriptor.module is None or descriptor.class_name is None:
        raise DriveRegistrationNotFoundError(
            f"registration {descriptor.name!r} has no import target (module/class)"
        )
    try:
        module = importlib.import_module(descriptor.module)
    except Exception as exc:
        raise DriveRegistrationNotFoundError(
            f"cannot import module {descriptor.module!r} for registration "
            f"{descriptor.name!r} (package {descriptor.source_package!r}): {exc}"
        ) from exc
    obj = getattr(module, descriptor.class_name, None)
    if obj is None:
        raise DriveRegistrationNotFoundError(
            f"class {descriptor.class_name!r} not found in module "
            f"{descriptor.module!r} for registration {descriptor.name!r}"
        )
    try:
        instance = obj() if isinstance(obj, type) else obj
    except Exception as exc:
        raise DriveValidationError(
            f"registration {descriptor.name!r} failed to instantiate: {exc}"
        ) from exc
    if not _looks_like_registration(instance):
        raise DriveValidationError(
            f"resolved object for {descriptor.name!r} is not a DriveRegistration "
            "(missing name/kind/schema_version)"
        )
    # Fail closed when the loaded implementation contradicts the manifest's
    # advertised identity / schema / roles / verifier contract (design §8.2, R1-18).
    descriptor.assert_implementation_compatible(_implementation_descriptor(instance))
    return instance


# ---------------------------------------------------------------------------
# internal helpers (shared with drive.snapshot)
# ---------------------------------------------------------------------------


def _looks_like_registration(obj: object) -> bool:
    return all(hasattr(obj, attr) for attr in ("name", "kind", "schema_version"))


def _implementation_descriptor(instance: object) -> DriveRegistrationDescriptor:
    """The descriptor a resolved registration instance advertises, or a shallow
    one built from its identity attributes when it exposes no ``descriptor()``."""
    describe = getattr(instance, "descriptor", None)
    if callable(describe):
        result = describe()
        if isinstance(result, DriveRegistrationDescriptor):
            return result
    return DriveRegistrationDescriptor(
        name=instance.name,
        kind=instance.kind,
        schema_version=getattr(instance, "schema_version", 1),
    )


def _opt_manifest_int(value: object, where: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise DriveValidationError(f"{where} must be an int")
    return value


def _opt_manifest_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DriveValidationError("prompt_contribution must be a string")
    return value


def _manifest_role_set(value: object, where: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple)):
        raise DriveValidationError(f"{where} must be a list of role names")
    return frozenset(str(role) for role in value)


def _manifest_dict(value: object, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DriveValidationError(f"{where} must be a mapping")
    return dict(value)


__all__ = [
    "AGGREGATE_PROMPT_MAX_BYTES",
    "ALL_ROLES",
    "DEFAULT_SPEC_MAX_BYTES",
    "EFFECTIVE_OPTIONS_ATTR",
    "PER_REGISTRATION_PROMPT_MAX_BYTES",
    "ROLE_METHODS",
    "DriveError",
    "DriveProjection",
    "DriveRegistration",
    "DriveRegistrationDescriptor",
    "GenericDriveRegistration",
    "Readiness",
    "VerificationResult",
    "apply_registration_options",
    "builtin_registrations",
    "effective_options",
    "resolve_registration",
]
