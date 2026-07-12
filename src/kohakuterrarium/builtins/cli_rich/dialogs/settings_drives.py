"""Drives tab for the rich-CLI settings overlay.

A self-contained section the :class:`SettingsOverlay` delegates to when the
active tab is ``Drives``. It edits the Studio-owned ``drive-settings.yaml``
through the same façade the web Settings page and ``kt config drive`` use
(:mod:`studio.identity.drive_settings`), so every surface stays in lock-step.

Save and Apply are deliberately distinct (design §8.6, §12.2): **Save** persists
validated config to the file; **Apply** attempts to apply it to the live engine
and reports ``applied_live`` / ``restart_required`` / ``rejected`` honestly — a
save never implies the running runtime changed. Advanced fields (byte budgets,
retry backoff, retention) stay editable via ``kt config`` / direct YAML and are
intentionally not surfaced here, matching the overlay's other tabs.
"""

from dataclasses import replace
from typing import Any, Callable

from rich.console import Group, RenderableType
from rich.text import Text

from kohakuterrarium.studio.identity.drive_settings import (
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

# Runtime int fields exposed inline (label, config attribute).
_INT_FIELDS: list[tuple[str, str]] = [
    ("Max active / creature", "max_active_per_creature"),
    ("Max pending / graph", "max_pending_per_graph"),
    ("Max consecutive drive turns", "max_consecutive_drive_turns"),
    ("Dispatcher concurrency", "dispatcher_concurrency"),
]

_APPLY_STYLE = {
    "applied_live": "green",
    "restart_required": "yellow",
    "rejected": "bright_red",
}


class DriveSettingsSection:
    """Runtime + registration editor rendered inside the ``Drives`` tab."""

    def __init__(self, get_engine: Callable[[], Any] | None = None) -> None:
        self._get_engine = get_engine or (lambda: None)
        self._settings = load_settings()
        self._status: dict[str, Any] = {}
        self._rows: list[dict[str, Any]] = []
        self.cursor = 0
        self.editing = False
        self._edit_buffer = ""
        self.flash = ""
        self.apply_result: dict[str, Any] | None = None

    # ── Data ────────────────────────────────────────────────────

    def reload(self) -> None:
        try:
            self._settings = load_settings()
        except DriveValidationError as exc:
            self._settings = None
            self.flash = f"settings file invalid: {exc}"
        self._status = settings_status()
        self._build_rows()
        self.editing = False
        self._edit_buffer = ""
        if self.cursor >= len(self._rows):
            self.cursor = max(0, len(self._rows) - 1)
        # Never rest the cursor on a non-interactive header row.
        while (
            self.cursor < len(self._rows)
            and self._rows[self.cursor]["type"] == "header"
        ):
            self.cursor += 1

    def _build_rows(self) -> None:
        rows: list[dict[str, Any]] = []
        runtime = self._runtime_values()
        rows.append({"type": "header", "label": "Runtime"})
        rows.append(
            {
                "type": "toggle",
                "key": "enabled",
                "label": "Runtime enabled",
                "value": bool(runtime.get("enabled", False)),
            }
        )
        for label, key in _INT_FIELDS:
            rows.append(
                {
                    "type": "int",
                    "key": key,
                    "label": label,
                    "value": runtime.get(key, 0),
                }
            )
        rows.append(
            {
                "type": "int",
                "key": "retry.max_attempts",
                "label": "Retry max attempts",
                "value": (runtime.get("retry") or {}).get("max_attempts", 0),
            }
        )
        rows.append({"type": "header", "label": "Registrations"})
        for reg in self._status.get("registrations", []):
            rows.append({"type": "registration", **reg})
        rows.append({"type": "action", "id": "save", "label": "Save to file"})
        rows.append(
            {"type": "action", "id": "apply", "label": "Apply to running engine"}
        )
        self._rows = rows

    def _runtime_values(self) -> dict[str, Any]:
        runtime = self._status.get("runtime")
        if isinstance(runtime, dict):
            return runtime
        return {}

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self._rows

    @property
    def parse_error(self) -> str | None:
        return self._status.get("parse_error")

    # ── Keyboard ────────────────────────────────────────────────

    def handle_key(self, key: str) -> bool:
        if self.editing:
            return self._edit_key(key)
        if key in ("up", "c-p"):
            self._move(-1)
            return True
        if key in ("down", "c-n"):
            self._move(1)
            return True
        if key == "enter":
            self._activate()
            return True
        return False

    def handle_text(self, char: str) -> bool:
        if self.editing:
            if char.isdigit():
                self._edit_buffer += char
            return True
        if char == "s":
            self._save()
            return True
        if char == "a":
            self._apply()
            return True
        if char == " ":
            self._activate()
            return True
        return True

    def _edit_key(self, key: str) -> bool:
        if key == "escape":
            self.editing = False
            self._edit_buffer = ""
            return True
        if key in ("backspace", "c-h"):
            self._edit_buffer = self._edit_buffer[:-1]
            return True
        if key == "enter":
            self._commit_edit()
            return True
        return True

    def _move(self, delta: int) -> None:
        if not self._rows:
            return
        idx = self.cursor
        while True:
            idx = max(0, min(len(self._rows) - 1, idx + delta))
            if self._rows[idx]["type"] != "header":
                break
            if idx in (0, len(self._rows) - 1):
                break
        self.cursor = idx
        self.flash = ""

    def _current(self) -> dict[str, Any] | None:
        if not self._rows:
            return None
        return self._rows[max(0, min(self.cursor, len(self._rows) - 1))]

    def _activate(self) -> None:
        row = self._current()
        if row is None:
            return
        kind = row["type"]
        if kind == "toggle":
            row["value"] = not row["value"]
        elif kind == "int":
            self.editing = True
            self._edit_buffer = str(row["value"])
        elif kind == "registration":
            row["enabled"] = not row.get("enabled", False)
        elif kind == "action":
            if row["id"] == "save":
                self._save()
            else:
                self._apply()

    def _commit_edit(self) -> None:
        row = self._current()
        self.editing = False
        if row is None or row["type"] != "int":
            self._edit_buffer = ""
            return
        try:
            row["value"] = int(self._edit_buffer) if self._edit_buffer else 0
        except ValueError:
            self.flash = "not a number"
        self._edit_buffer = ""

    # ── Save / apply ────────────────────────────────────────────

    def _save(self) -> None:
        if self._settings is None:
            self.flash = "cannot save over an invalid settings file; fix it first"
            return
        try:
            new_settings = self._collect_settings()
        except (ValueError, DriveValidationError) as exc:
            self.flash = f"invalid: {exc}"
            return
        try:
            saved = save_settings(
                new_settings,
                expected_revision=self._settings.revision,
                expected_exists=self._settings.revision is not None,
            )
        except DriveSettingsConflictError:
            self.flash = "settings changed on disk — reloaded, re-apply your edits"
            self.reload()
            return
        except DriveValidationError as exc:
            self.flash = f"invalid: {exc}"
            return
        self._settings = saved.settings
        self.apply_result = None
        self.flash = (
            "saved (not yet applied; WARNING: file-only durability)"
            if saved.durability.value == "file_only"
            else "saved (not yet applied to the running engine)"
        )
        self.reload()

    def _apply(self) -> None:
        engine = self._get_engine()
        try:
            self.apply_result = apply_runtime(engine)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("drive settings apply failed", error=str(exc))
            self.apply_result = {"result": "rejected", "warnings": [str(exc)]}
        result = (self.apply_result or {}).get("result", "rejected")
        self.flash = f"apply: {result}"

    def _collect_settings(self) -> Any:
        runtime = self._settings.runtime
        overrides: dict[str, Any] = {}
        retry_attempts: int | None = None
        for row in self._rows:
            if row["type"] == "toggle" and row["key"] == "enabled":
                overrides["enabled"] = bool(row["value"])
            elif row["type"] == "int":
                if row["key"] == "retry.max_attempts":
                    retry_attempts = int(row["value"])
                else:
                    overrides[row["key"]] = int(row["value"])
        new_runtime = replace(runtime, **overrides)
        if retry_attempts is not None:
            new_runtime = replace(
                new_runtime, retry=replace(runtime.retry, max_attempts=retry_attempts)
            )
        new_regs = dict(self._settings.registrations)
        for row in self._rows:
            if row["type"] != "registration":
                continue
            name = row["name"]
            existing = new_regs.get(name) or RegistrationSetting()
            new_regs[name] = replace(existing, enabled=bool(row.get("enabled", False)))
        return replace(self._settings, runtime=new_runtime, registrations=new_regs)

    # ── Rendering ───────────────────────────────────────────────

    def render_body(self) -> RenderableType:
        if self.parse_error:
            return Text(
                f"  settings file error: {self.parse_error}", style="bright_red"
            )
        out: list[RenderableType] = []
        for i, row in enumerate(self._rows):
            out.append(_render_setting_row(row, i == self.cursor, self))
        if self.apply_result:
            out.append(Text(""))
            out.append(_render_apply_result(self.apply_result))
        return Group(*out)


def _render_setting_row(
    row: dict[str, Any], selected: bool, section: DriveSettingsSection
) -> Text:
    kind = row["type"]
    if kind == "header":
        return Text(f"  {row['label']}", style="bold magenta")
    line = Text()
    line.append(
        "  › " if selected else "    ", style="bold bright_cyan" if selected else "dim"
    )
    if kind == "toggle":
        line.append(f"{row['label']}: ", style="bold" if selected else "")
        on = row["value"]
        line.append("[on]" if on else "[off]", style="green" if on else "bright_black")
    elif kind == "int":
        line.append(f"{row['label']}: ", style="bold" if selected else "")
        if selected and section.editing:
            line.append(section._edit_buffer or "", style="cyan")
            line.append("█", style="cyan")
        else:
            line.append(str(row["value"]), style="cyan")
    elif kind == "registration":
        enabled = row.get("enabled", False)
        line.append(
            "[on] " if enabled else "[off]",
            style="green" if enabled else "bright_black",
        )
        line.append(f" {row['name']}", style="bold" if selected else "")
        line.append(f"  ({row.get('kind', '?')})", style="magenta")
        source = row.get("source", "")
        if source:
            line.append(f"  {source}", style="dim")
        if row.get("conflict"):
            line.append("  [conflict]", style="bright_red")
        if row.get("error"):
            line.append("  [load error]", style="bright_red")
        elif enabled and row.get("loaded"):
            line.append("  [loaded]", style="green")
    elif kind == "action":
        line.append(row["label"], style="bold green" if selected else "green")
    return line


def _render_apply_result(result: dict[str, Any]) -> RenderableType:
    outcome = result.get("result", "rejected")
    style = _APPLY_STYLE.get(outcome, "white")
    rows: list[RenderableType] = [
        Text(f"  apply result: {outcome}", style=f"bold {style}"),
    ]
    running = result.get("running_revision")
    desired = result.get("desired_revision")
    if running is not None or desired is not None:
        rows.append(
            Text(
                f"    desired={_short(desired)}  running={_short(running)}",
                style="dim",
            )
        )
    for warning in result.get("warnings", []) or []:
        rows.append(Text(f"    ! {warning}", style="yellow"))
    return Group(*rows)


def _short(rev: Any) -> str:
    if not rev:
        return "-"
    return str(rev)[:8]


__all__ = ["DriveSettingsSection"]
