"""Immutable enabled-registry snapshot + derived availability (design §8.2, §8.6).

The :class:`EnabledRegistrySnapshot` is the collision-checked, in-process view a
running Terrarium holds of its enabled registrations. It is built once (or on an
applied registry swap) from live registration instances; prompt contributions
are normalized against per-registration and aggregate byte budgets at build time
(§8.7), so prompt rendering never re-runs registration code.

Availability of a persisted record against a snapshot is a *derived* condition
(§8.6): ``registration_disabled`` / ``registration_unavailable`` /
``registration_incompatible`` — never a :class:`DriveStatus`, and computed by a
pure function that never mutates the record.

Leaf module: builds on :mod:`drive.registration` plus stdlib and the logger.
"""

from dataclasses import dataclass

from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.models import DriveAvailability, DriveRecord
from kohakuterrarium.terrarium.drive.registration import (
    AGGREGATE_PROMPT_MAX_BYTES,
    PER_REGISTRATION_PROMPT_MAX_BYTES,
    ROLE_METHODS,
    DriveRegistration,
    DriveRegistrationDescriptor,
    _looks_like_registration,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class EnabledRegistration:
    """One entry in the enabled snapshot: descriptor + live instance + status.

    ``registration`` is a live in-process object, so the snapshot is in-process
    only and never wire-serialized. ``available`` is False when a required role
    is missing/broken; ``prompt_text`` is the normalized, budget-checked prose.
    """

    descriptor: DriveRegistrationDescriptor
    registration: DriveRegistration
    available: bool
    unavailable_reason: str | None
    prompt_text: str | None


@dataclass
class _BuildEntry:
    descriptor: DriveRegistrationDescriptor
    registration: DriveRegistration
    available: bool
    reason: str | None
    prompt_text: str | None = None


@dataclass(frozen=True)
class EnabledRegistrySnapshot:
    """Immutable, collision-checked view of the enabled registrations (§8.2).

    Duplicate ``name`` and two registrations claiming one ``kind`` are hard
    errors at :meth:`build`. Lookups are linear over the (small) entry tuple.
    """

    entries: tuple[EnabledRegistration, ...] = ()

    @classmethod
    def build(
        cls,
        registrations: "list[DriveRegistration] | tuple[DriveRegistration, ...]",
        *,
        per_prompt_max_bytes: int = PER_REGISTRATION_PROMPT_MAX_BYTES,
        aggregate_prompt_max_bytes: int = AGGREGATE_PROMPT_MAX_BYTES,
    ) -> "EnabledRegistrySnapshot":
        prelim: list[_BuildEntry] = []
        by_name: dict[str, _BuildEntry] = {}
        by_kind: dict[str, list[_BuildEntry]] = {}
        for reg in registrations:
            descriptor = _descriptor_of(reg)
            name = descriptor.name
            if name in by_name:
                raise DriveValidationError(
                    f"duplicate drive registration name {name!r}: "
                    f"{_prov(by_name[name].descriptor)} and {_prov(descriptor)}"
                )
            missing = _missing_required_roles(reg, descriptor)
            available = not missing
            reason = (
                None if available else f"missing required role(s): {', '.join(missing)}"
            )
            entry = _BuildEntry(descriptor, reg, available, reason)
            by_name[name] = entry
            by_kind.setdefault(descriptor.kind, []).append(entry)
            prelim.append(entry)

        for kind, claimants in by_kind.items():
            if len(claimants) > 1:
                provs = ", ".join(_prov(e.descriptor) for e in claimants)
                raise DriveValidationError(
                    f"multiple enabled registrations claim kind {kind!r}: {provs}"
                )

        _normalize_prompts(prelim, per_prompt_max_bytes, aggregate_prompt_max_bytes)

        frozen = tuple(
            EnabledRegistration(
                descriptor=e.descriptor,
                registration=e.registration,
                available=e.available,
                unavailable_reason=e.reason,
                prompt_text=e.prompt_text,
            )
            for e in prelim
        )
        return cls(frozen)

    def get(self, name: str) -> EnabledRegistration | None:
        for entry in self.entries:
            if entry.descriptor.name == name:
                return entry
        return None

    def for_kind(self, kind: str) -> EnabledRegistration | None:
        """The enabled entry serving ``kind`` (available or not); kinds are
        unique per snapshot so at most one is returned."""
        for entry in self.entries:
            if entry.descriptor.kind == kind:
                return entry
        return None

    def available_kinds(self) -> frozenset[str]:
        return frozenset(e.descriptor.kind for e in self.entries if e.available)

    def prompt_contributions(self) -> tuple[tuple[str, str], ...]:
        """``(name, prompt_text)`` for available entries with prose, name-ordered."""
        pairs = [
            (e.descriptor.name, e.prompt_text)
            for e in self.entries
            if e.available and e.prompt_text
        ]
        return tuple(sorted(pairs, key=lambda p: p[0]))

    def availability_for(self, record: DriveRecord) -> DriveAvailability:
        return derive_availability(record, self)

    @property
    def is_empty(self) -> bool:
        return not self.entries


# ---------------------------------------------------------------------------
# Derived availability (design §8.6) — pure, never mutates the record
# ---------------------------------------------------------------------------


def availability_for_kind(
    kind: str, schema_version: int, snapshot: EnabledRegistrySnapshot
) -> DriveAvailability:
    """Availability of ``kind``@``schema_version`` against the enabled snapshot."""
    entry = snapshot.for_kind(kind)
    if entry is None:
        return DriveAvailability.REGISTRATION_DISABLED
    if not entry.available:
        return DriveAvailability.REGISTRATION_UNAVAILABLE
    if not entry.descriptor.supports_schema(schema_version):
        return DriveAvailability.REGISTRATION_INCOMPATIBLE
    return DriveAvailability.AVAILABLE


def derive_availability(
    record: DriveRecord, snapshot: EnabledRegistrySnapshot
) -> DriveAvailability:
    """Availability of a persisted ``record`` against ``snapshot`` (§8.6)."""
    return availability_for_kind(record.kind, record.schema_version, snapshot)


# ---------------------------------------------------------------------------
# build helpers
# ---------------------------------------------------------------------------


def _prov(descriptor: DriveRegistrationDescriptor) -> str:
    return f"{descriptor.source_package or 'builtin'}/{descriptor.name}"


def _descriptor_of(reg: DriveRegistration) -> DriveRegistrationDescriptor:
    describe = getattr(reg, "descriptor", None)
    if callable(describe):
        result = describe()
        if isinstance(result, DriveRegistrationDescriptor):
            return result
        raise DriveValidationError(
            f"registration {getattr(reg, 'name', '?')!r} descriptor() did not return "
            "a DriveRegistrationDescriptor"
        )
    if not _looks_like_registration(reg):
        raise DriveValidationError(
            "registration must expose name/kind/schema_version or descriptor()"
        )
    return DriveRegistrationDescriptor(
        name=reg.name, kind=reg.kind, schema_version=getattr(reg, "schema_version", 1)
    )


def _missing_required_roles(
    reg: DriveRegistration, descriptor: DriveRegistrationDescriptor
) -> list[str]:
    missing: list[str] = []
    for role in sorted(descriptor.required_roles):
        method = ROLE_METHODS.get(role)
        if method is None or not callable(getattr(reg, method, None)):
            missing.append(role)
    return missing


def _extract_prompt(
    reg: DriveRegistration, descriptor: DriveRegistrationDescriptor
) -> str | None:
    raw: object
    contribute = getattr(reg, "prompt_contribution", None)
    if callable(contribute):
        try:
            raw = contribute()
        except Exception as exc:
            logger.warning(
                "drive registration prompt_contribution() failed; omitting prose",
                reg_name=descriptor.name,
                error=str(exc),
            )
            return None
    else:
        raw = descriptor.prompt_contribution
    if raw is None:
        return None
    if not isinstance(raw, str):
        logger.warning(
            "drive registration prompt is not a string; omitting",
            reg_name=descriptor.name,
        )
        return None
    text = raw.strip()
    return text or None


def _normalize_prompts(
    prelim: list[_BuildEntry], per_cap: int, aggregate_cap: int
) -> None:
    aggregate = 0
    for entry in sorted(prelim, key=lambda e: e.descriptor.name):
        if not entry.available:
            continue
        text = _extract_prompt(entry.registration, entry.descriptor)
        if text is None:
            # A REQUIRED prompt whose callable raised, returned ``None``, or
            # returned a non-string yields no prose. That must fail closed —
            # unavailable, exactly like a byte-budget overflow — never a silently
            # prompt-less registration that still declares prompt required (R1-27
            # edge). An optional prompt may be absent without penalty.
            if "prompt" in entry.descriptor.required_roles:
                entry.available = False
                entry.reason = (
                    "required prompt contribution is missing, non-string, or raised"
                )
            continue
        size = len(text.encode("utf-8"))
        if size > per_cap:
            if "prompt" in entry.descriptor.required_roles:
                entry.available = False
                entry.reason = (
                    f"required prompt contribution is {size} bytes, over the "
                    f"{per_cap}-byte cap"
                )
            else:
                logger.warning(
                    "drive registration prompt omitted (over per-registration cap)",
                    reg_name=entry.descriptor.name,
                    size=size,
                    cap=per_cap,
                )
            continue
        if aggregate + size > aggregate_cap:
            # A REQUIRED prompt that does not fit the aggregate budget fails
            # closed, exactly like the per-registration cap (R1-27); only an
            # optional prompt may be silently omitted.
            if "prompt" in entry.descriptor.required_roles:
                entry.available = False
                entry.reason = (
                    f"required prompt contribution does not fit the {aggregate_cap}-"
                    "byte aggregate prompt budget"
                )
            else:
                logger.warning(
                    "drive registration prompt omitted (over aggregate budget)",
                    reg_name=entry.descriptor.name,
                    size=size,
                )
            continue
        aggregate += size
        entry.prompt_text = text


__all__ = [
    "EnabledRegistration",
    "EnabledRegistrySnapshot",
    "availability_for_kind",
    "derive_availability",
]
