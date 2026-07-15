"""Studio catalog view of Drive registrations (design §8.2).

Aggregates built-in and package-declared Drive registrations into read-only
catalog payloads. Discovery remains import-free so merely listing available
registrations cannot execute package code; explicit validation or instantiation
crosses the import boundary.

Collision invariants:

- a duplicate registration ``name`` (across packages, or builtin vs package) is
  a hard validation error;
- two registrations claiming one ``kind`` are surfaced as a catalog *conflict*
  on every involved entry, even when neither is enabled.
"""

from kohakuterrarium.packages.resolve import ensure_package_importable
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.terrarium.drive.errors import (
    DriveError,
    DriveRegistrationNotFoundError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration
from kohakuterrarium.terrarium.drive.registration import (
    DriveRegistration,
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
    apply_registration_options,
    effective_options,
    resolve_registration,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _builtin_registrations() -> list[DriveRegistration]:
    """Construct fresh stateless instances of the built-in registrations.

    Availability does not imply enablement; runtime settings select which
    registrations participate.
    """
    return [GenericDriveRegistration(), GoalDriveRegistration()]


def builtin_descriptors() -> list[DriveRegistrationDescriptor]:
    """Descriptors for the framework's builtin registrations."""
    return [r.descriptor() for r in _builtin_registrations()]


def installed_descriptors() -> list[DriveRegistrationDescriptor]:
    """Scan package manifests into provenance-aware descriptors.

    Duplicate names are ambiguous and therefore fail the catalog. Malformed
    individual entries are logged and skipped so one package cannot hide every
    valid registration; explicit validation reports implementation load errors.
    """
    out: list[DriveRegistrationDescriptor] = []
    seen: dict[str, str] = {}
    for pkg in list_packages():
        pkg_name = pkg.get("name", "?")
        for entry in pkg.get("drive_registrations", []) or []:
            if not isinstance(entry, dict):
                continue
            reg_name = entry.get("name")
            if not reg_name:
                continue
            if reg_name in seen:
                raise DriveValidationError(
                    f"duplicate drive registration name {reg_name!r}: declared by "
                    f"packages [{seen[reg_name]}, {pkg_name}]"
                )
            try:
                descriptor = DriveRegistrationDescriptor.from_manifest(pkg_name, entry)
            except DriveValidationError as exc:
                logger.warning(
                    "skipping malformed drive_registrations entry",
                    pkg=pkg_name,
                    reg_name=reg_name,
                    error=str(exc),
                )
                continue
            seen[reg_name] = pkg_name
            out.append(descriptor)
    return out


def list_drive_registrations() -> list[dict]:
    """Catalog DTOs for every available Drive registration, name-ordered.

    Raises :class:`DriveValidationError` on a duplicate name (builtin+installed);
    kind conflicts are flagged on the DTOs rather than raised.
    """
    descriptors = builtin_descriptors() + installed_descriptors()

    names: dict[str, DriveRegistrationDescriptor] = {}
    for d in descriptors:
        if d.name in names:
            raise DriveValidationError(
                f"duplicate drive registration name {d.name!r}: "
                f"{_provenance(names[d.name])} and {_provenance(d)}"
            )
        names[d.name] = d

    kind_claims: dict[str, list[DriveRegistrationDescriptor]] = {}
    for d in descriptors:
        kind_claims.setdefault(d.kind, []).append(d)

    entries: list[dict] = []
    for d in descriptors:
        claimants = kind_claims[d.kind]
        conflict = len(claimants) > 1
        reason = None
        if conflict:
            provs = ", ".join(sorted(_provenance(c) for c in claimants))
            reason = f"kind {d.kind!r} is claimed by multiple registrations: {provs}"
        entries.append(_to_dto(d, conflict=conflict, conflict_reason=reason))

    entries.sort(key=lambda e: e["name"])
    return entries


def get_drive_registration(name: str) -> dict | None:
    """The catalog DTO for one registration by ``name``, or ``None``."""
    for entry in list_drive_registrations():
        if entry["name"] == name:
            return entry
    return None


def validate_drive_registration(name: str) -> dict | None:
    """Explicitly import one registration and report whether it loads.

    Catalog discovery stays import-free; this validation call is the deliberate
    boundary where package implementation code may execute. Unknown names return
    ``None``.
    """
    for d in builtin_descriptors():
        if d.name == name:
            dto = _to_dto(d, conflict=False, conflict_reason=None)
            dto.update(loaded=True, error=None)
            return dto
    for d in installed_descriptors():
        if d.name == name:
            dto = _to_dto(d, conflict=False, conflict_reason=None)
            try:
                registration = _resolve_installed(d)
            except DriveError as exc:
                dto.update(loaded=False, error=str(exc))
                return dto
            dto.update(loaded=True, error=None, kind=registration.kind)
            return dto
    return None


def _resolve_installed(descriptor: DriveRegistrationDescriptor) -> DriveRegistration:
    """Make the owning package importable before resolving its implementation.

    Installed package roots need not already be on ``sys.path`` because discovery
    is import-free. Validation and enablement must establish that path before the
    descriptor's module can be imported.
    """
    if descriptor.source_package:
        ensure_package_importable(descriptor.source_package)
    return resolve_registration(descriptor)


def instantiate_registration(
    name: str, options: dict | None = None
) -> DriveRegistration:
    """Instantiate a named registration with descriptor-validated options.

    Built-ins are constructed directly; package registrations are imported only
    when selected. Unsupported or invalid options raise
    :class:`DriveValidationError`, and unknown names raise
    :class:`DriveRegistrationNotFoundError`.
    """
    for registration in _builtin_registrations():
        if registration.name == name:
            apply_registration_options(registration, registration.descriptor(), options)
            return registration
    for descriptor in installed_descriptors():
        if descriptor.name == name:
            registration = _resolve_installed(descriptor)
            apply_registration_options(registration, descriptor, options)
            return registration
    raise DriveRegistrationNotFoundError(
        f"no installed drive registration named {name!r}"
    )


def list_drive_registrations_status(
    enabled_names: set[str], options_by_name: dict[str, dict] | None = None
) -> list[dict]:
    """Annotate discovered registrations with runtime selection and load status.

    Disabled registrations remain import-free. Enabled entries are instantiated
    with node-specific options so ``loaded``, ``error``, and
    ``effective_options`` reflect the actual runtime configuration.
    """
    options_by_name = options_by_name or {}
    entries = list_drive_registrations()
    for entry in entries:
        name = entry["name"]
        enabled = name in enabled_names
        entry["enabled"] = enabled
        entry["loaded"] = None
        entry["error"] = None
        entry["effective_options"] = None
        if not enabled:
            continue
        try:
            registration = instantiate_registration(name, options_by_name.get(name))
        except DriveError as exc:
            entry["loaded"] = False
            entry["error"] = str(exc)
            continue
        entry["loaded"] = True
        entry["kind"] = registration.kind
        entry["effective_options"] = effective_options(registration)
    return entries


def _provenance(descriptor: DriveRegistrationDescriptor) -> str:
    return f"{descriptor.source_package or 'builtin'}/{descriptor.name}"


def _to_dto(
    descriptor: DriveRegistrationDescriptor,
    *,
    conflict: bool,
    conflict_reason: str | None,
) -> dict:
    dto = descriptor.to_dict()
    dto.update(
        source="builtin" if descriptor.source_package is None else "package",
        package=descriptor.source_package,
        conflict=conflict,
        conflict_reason=conflict_reason,
    )
    return dto


__all__ = [
    "builtin_descriptors",
    "get_drive_registration",
    "installed_descriptors",
    "instantiate_registration",
    "list_drive_registrations",
    "list_drive_registrations_status",
    "validate_drive_registration",
]
