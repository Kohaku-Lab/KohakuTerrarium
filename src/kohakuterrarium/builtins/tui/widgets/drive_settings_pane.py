"""Textual pane for Drive runtime + registration settings.

Embedded in the ``Settings`` tab of :class:`DriveScreen`. Edits the Studio-owned
``drive-settings.yaml`` through the same façade the rich CLI, web Settings, and
``kt config drive`` use, so every surface stays in lock-step. Save and Apply are
distinct (design §8.6, §12.2): **Save** persists validated config; **Apply**
attempts a live application and reports ``applied_live`` / ``restart_required`` /
``rejected`` honestly. Advanced fields (byte budgets, backoff, retention) remain
``kt config`` / YAML editable and are intentionally omitted here.

``build_settings_from_values`` is a pure helper so the collect+persist logic is
unit-testable without mounting the Textual app.
"""

from dataclasses import replace
from typing import Any, Callable

from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, Static, Switch

from kohakuterrarium.studio.identity.drive_settings import (
    DriveSettings,
    RegistrationSetting,
    apply_runtime,
    load_settings,
    save_settings,
    settings_status,
)
from kohakuterrarium.terrarium.drive.errors import (
    DriveSettingsConflictError,
    DriveValidationError,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_INT_FIELDS: list[tuple[str, str]] = [
    ("Max active / creature", "max_active_per_creature"),
    ("Max pending / graph", "max_pending_per_graph"),
    ("Max consecutive drive turns", "max_consecutive_drive_turns"),
    ("Dispatcher concurrency", "dispatcher_concurrency"),
    ("Retry max attempts", "retry.max_attempts"),
]


def build_settings_from_values(
    base: DriveSettings, values: dict[str, Any]
) -> DriveSettings:
    """Fold edited widget ``values`` into a new :class:`DriveSettings`.

    Preserves every field the pane does not expose (byte budgets, backoff,
    retention, per-registration options). ``values`` carries ``enabled``, the
    int tuning fields (dotted ``retry.max_attempts`` handled specially), and a
    ``registrations`` name->enabled map.
    """
    runtime = base.runtime
    overrides: dict[str, Any] = {}
    retry_attempts: int | None = None
    if "enabled" in values:
        overrides["enabled"] = bool(values["enabled"])
    for _, key in _INT_FIELDS:
        if key not in values:
            continue
        if key == "retry.max_attempts":
            retry_attempts = int(values[key])
        else:
            overrides[key] = int(values[key])
    new_runtime = replace(runtime, **overrides)
    if retry_attempts is not None:
        new_runtime = replace(
            new_runtime, retry=replace(runtime.retry, max_attempts=retry_attempts)
        )
    new_regs = dict(base.registrations)
    for name, enabled in (values.get("registrations") or {}).items():
        existing = new_regs.get(name) or RegistrationSetting()
        new_regs[name] = replace(existing, enabled=bool(enabled))
    return replace(base, runtime=new_runtime, registrations=new_regs)


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)


class DriveSettingsPane(VerticalScroll):
    """Runtime + registration editor bound to ``drive-settings.yaml``."""

    def __init__(self, get_engine: Callable[[], Any] | None = None) -> None:
        super().__init__()
        self._get_engine = get_engine or (lambda: None)
        self._settings: DriveSettings | None = None
        self._status: dict[str, Any] = {}
        self._reg_switches: list[tuple[str, str]] = []  # (name, switch id)
        try:
            self._settings = load_settings()
        except DriveValidationError:
            self._settings = None
        self._status = settings_status()

    def compose(self):
        if self._status.get("parse_error"):
            yield Static(
                f"settings file error: {self._status['parse_error']}",
                classes="drive-error",
            )
            return
        runtime = self._status.get("runtime") or {}
        yield Static("[b]Runtime[/b]")
        with Horizontal(classes="drive-field"):
            yield Label("Enabled")
            yield Switch(value=bool(runtime.get("enabled", False)), id="drive-enabled")
        for label, key in _INT_FIELDS:
            value = _lookup(runtime, key)
            with Horizontal(classes="drive-field"):
                yield Label(label)
                yield Input(
                    value=str(value), id=f"drive-int-{_safe(key)}", type="integer"
                )
        yield Static("[b]Registrations[/b]")
        for reg in self._status.get("registrations", []):
            name = reg["name"]
            sw_id = f"drive-reg-{_safe(name)}"
            self._reg_switches.append((name, sw_id))
            with Horizontal(classes="drive-field"):
                yield Switch(value=bool(reg.get("enabled")), id=sw_id)
                yield Label(_reg_label(reg))
        with Horizontal(classes="drive-actions"):
            yield Button("Save to file", id="drive-save", variant="primary")
            yield Button("Apply to engine", id="drive-apply", variant="warning")
        yield Static("", id="drive-settings-status")

    # ── Actions ─────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "drive-save":
            self._save()
        elif event.button.id == "drive-apply":
            self._apply()

    def _collect_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        values["enabled"] = self.query_one("#drive-enabled", Switch).value
        for _, key in _INT_FIELDS:
            widget = self.query_one(f"#drive-int-{_safe(key)}", Input)
            text = (widget.value or "").strip()
            if text:
                values[key] = int(text)
        regs: dict[str, bool] = {}
        for name, sw_id in self._reg_switches:
            regs[name] = self.query_one(f"#{sw_id}", Switch).value
        values["registrations"] = regs
        return values

    def _save(self) -> None:
        if self._settings is None:
            self._set_status("[red]cannot save over an invalid settings file[/red]")
            return
        try:
            new_settings = build_settings_from_values(
                self._settings, self._collect_values()
            )
        except (ValueError, DriveValidationError) as exc:
            self._set_status(f"[red]invalid: {exc}[/red]")
            return
        try:
            saved = save_settings(
                new_settings,
                expected_revision=self._settings.revision,
                expected_exists=self._settings.revision is not None,
            )
        except DriveSettingsConflictError:
            self._settings = load_settings()
            self._set_status(
                "[yellow]changed on disk — reloaded; re-apply edits[/yellow]"
            )
            return
        except DriveValidationError as exc:
            self._set_status(f"[red]invalid: {exc}[/red]")
            return
        self._settings = saved.settings
        if saved.durability.value == "file_only":
            self._set_status(
                "[yellow]saved with FILE_ONLY durability; directory rename is not "
                "power-loss protected[/yellow]"
            )
        else:
            self._set_status(
                "[green]saved[/green] (not yet applied to the running engine)"
            )

    def _apply(self) -> None:
        try:
            result = apply_runtime(self._get_engine())
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("drive settings apply failed", error=str(exc))
            self._set_status(f"[red]apply rejected: {exc}[/red]")
            return
        outcome = result.get("result", "rejected")
        colour = {
            "applied_live": "green",
            "restart_required": "yellow",
            "rejected": "red",
        }.get(outcome, "white")
        warnings = "; ".join(result.get("warnings", []) or [])
        suffix = f" — {warnings}" if warnings else ""
        self._set_status(f"[{colour}]apply: {outcome}[/{colour}]{suffix}")

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#drive-settings-status", Static).update(markup)
        except Exception:  # pragma: no cover — before mount
            pass


def _lookup(runtime: dict[str, Any], key: str) -> Any:
    if key == "retry.max_attempts":
        return (runtime.get("retry") or {}).get("max_attempts", 0)
    return runtime.get(key, 0)


def _reg_label(reg: dict[str, Any]) -> str:
    parts = [reg["name"], f"({reg.get('kind', '?')})"]
    if reg.get("conflict"):
        parts.append("[red]conflict[/red]")
    if reg.get("error"):
        parts.append("[red]load error[/red]")
    elif reg.get("enabled") and reg.get("loaded"):
        parts.append("[green]loaded[/green]")
    source = reg.get("source")
    if source:
        parts.append(f"[dim]{source}[/dim]")
    return "  ".join(parts)


__all__ = ["DriveSettingsPane", "build_settings_from_values"]
