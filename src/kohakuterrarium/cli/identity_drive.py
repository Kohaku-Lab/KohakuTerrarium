"""``kt config drive`` — operator CLI over the Studio Drive settings façade.

Thin terminal wrapper around :mod:`kohakuterrarium.studio.identity.drive_settings`:
``show`` / ``set`` / ``registrations`` manage the canonical
``drive-settings.yaml``; ``apply`` validates that the saved settings resolve. A
raw save is deliberately distinct from live apply — the CLI has no running engine
to swap, so ``apply`` reports validity + ``restart_required`` rather than claiming
a live runtime changed.
"""

from typing import Any

from kohakuterrarium.studio.identity import drive_settings as ds
from kohakuterrarium.terrarium.drive.errors import DriveError


def show_cli() -> int:
    """Print the runtime + per-registration enabled state from the settings file."""
    status = ds.settings_status()
    if status.get("parse_error"):
        print(f"drive-settings.yaml is invalid: {status['parse_error']}")
        return 1
    runtime = status.get("runtime") or {}
    print(f"path:     {ds.drive_settings_path()}")
    print(f"revision: {status.get('settings_revision')}")
    print(f"runtime.enabled: {runtime.get('enabled', False)}")
    for key in (
        "max_active_per_creature",
        "max_pending_per_graph",
        "max_consecutive_drive_turns",
        "dispatcher_concurrency",
    ):
        if key in runtime:
            print(f"runtime.{key}: {runtime[key]}")
    _print_registrations(status.get("registrations") or [])
    return 0


def registrations_cli() -> int:
    """List available Drive registrations and their enabled/load state."""
    status = ds.settings_status()
    if status.get("parse_error"):
        print(f"drive-settings.yaml is invalid: {status['parse_error']}")
        return 1
    regs = status.get("registrations") or []
    if not regs:
        print("No Drive registrations installed.")
        return 0
    _print_registrations(regs)
    return 0


def set_cli(field: str | None, value: str | None) -> int:
    """Set a runtime field or toggle a registration, then save (CAS-checked).

    ``field`` is either ``registration:<name>`` (value on/off) or a
    ``runtime.<name>`` / bare runtime field name (value coerced to bool/int/str).
    """
    if not field or value is None:
        print("usage: kt config drive set <field> <value>")
        print("  e.g. kt config drive set enabled true")
        print("       kt config drive set max_active_per_creature 8")
        print("       kt config drive set registration:generic on")
        return 2
    try:
        settings = ds.load_settings()
    except DriveError as exc:
        print(f"cannot load settings: {exc}")
        return 1
    data = ds.settings_to_dict(settings)
    try:
        _apply_set(data, field, value)
        saved = ds.save_settings(
            data,
            expected_revision=settings.revision,
            expected_exists=settings.revision is not None,
        )
    except DriveError as exc:
        print(f"invalid setting: {exc}")
        return 1
    print(
        f"saved (revision {saved.settings.revision}). Run 'kt config drive apply' or "
        "restart the running server to apply."
    )
    if saved.durability is ds.SaveDurability.FILE_ONLY:
        print(
            "WARNING: saved with file-only durability; the directory rename could "
            "not be fsync-protected against sudden power loss."
        )
    return 0


def apply_cli() -> int:
    """Validate that the saved settings resolve; report live-apply expectation.

    The CLI owns no running engine, so a successful resolve is reported as
    ``restart_required`` for any running server; the file takes effect on the
    next engine start.
    """
    try:
        spec = ds.resolve_runtime()
    except DriveError as exc:
        print(f"rejected: {exc}")
        return 1
    if not spec.config.enabled:
        print("applied_live: Drive runtime is disabled in settings.")
        return 0
    names = sorted(r.name for r in spec.registrations) if spec.registrations else []
    print(
        "restart_required: settings are valid"
        + (f" (enabled: {', '.join(names)})" if names else "")
        + "; a running server picks them up on restart."
    )
    return 0


def _apply_set(data: dict[str, Any], field: str, value: str) -> None:
    """Apply one CLI field assignment to the settings mapping."""
    if field.startswith("registration:"):
        name = field.split(":", 1)[1]
        if not name:
            raise DriveError("registration name is required")
        enabled = _coerce_bool(value)
        regs = data.setdefault("registrations", {})
        entry = regs.get(name) or {}
        entry["enabled"] = enabled
        entry.setdefault("options", {})
        regs[name] = entry
        return
    key = field.split(".", 1)[1] if field.startswith("runtime.") else field
    runtime = data.setdefault("runtime", {})
    runtime[key] = _coerce_scalar(value)


def _coerce_bool(value: str) -> bool:
    """Parse accepted CLI boolean spellings."""
    lowered = value.strip().lower()
    if lowered in ("true", "on", "1", "yes"):
        return True
    if lowered in ("false", "off", "0", "no"):
        return False
    raise DriveError(f"expected a boolean (on/off), got {value!r}")


def _coerce_scalar(value: str) -> Any:
    """Coerce a CLI value to a boolean or integer when possible."""
    lowered = value.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        return value


def _print_registrations(regs: list[dict[str, Any]]) -> None:
    """Print registration status rows."""
    if not regs:
        return
    print("registrations:")
    for reg in regs:
        name = reg.get("name", "?")
        enabled = "on " if reg.get("enabled") else "off"
        available = reg.get("available")
        kind = reg.get("kind", "")
        status = reg.get("load_status") or ("available" if available else "installed")
        print(f"  [{enabled}] {name:<16} kind={kind:<12} {status}")


__all__ = [
    "apply_cli",
    "registrations_cli",
    "set_cli",
    "show_cli",
]
