"""``app-settings.json`` read / write / validate.

Schema lives in this module so the launcher can validate without
pulling pydantic (launcher is stdlib-only + ``pip`` + ``packaging``).
Invalid fields fall back to defaults with a one-line warning so a
hand-edited file never wedges the wrapper on a parse error.

Settings shape (see ``plans/1.5.0-roadmap/06-app-update/design.md`` §3
for the canonical reference):

.. code-block:: json

    {
      "source": {
        "kind": "pypi" | "git" | "local" | "bundled",
        "spec": null | "<spec>",
        "extras": ["full", ...]
      },
      "update": {
        "mode": "manual" | "notify-on-launch" | "auto-on-launch",
        "check-cache-hours": 24
      },
      "runtime": {
        "venv-path": null | "<path>",
        "last-installed-version": null | "<pep440>",
        "last-check-at": null | "<iso8601>"
      }
    }
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import settings_path, venv_dir

SOURCE_KINDS = ("pypi", "git", "local", "bundled")
UPDATE_MODES = ("manual", "notify-on-launch", "auto-on-launch")

DEFAULT_CHECK_CACHE_HOURS = 24


@dataclass
class SourceConfig:
    kind: str = "pypi"
    spec: str | None = None
    extras: list[str] = field(default_factory=list)


@dataclass
class UpdateConfig:
    mode: str = "notify-on-launch"
    check_cache_hours: int = DEFAULT_CHECK_CACHE_HOURS


@dataclass
class RuntimeConfig:
    venv_path: str | None = None
    last_installed_version: str | None = None
    last_check_at: str | None = None


@dataclass
class AppSettings:
    source: SourceConfig = field(default_factory=SourceConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def _coerce_source(raw: Any, log) -> SourceConfig:
    if not isinstance(raw, dict):
        log.warning("settings: source is not a dict; resetting to defaults")
        return SourceConfig()
    kind = raw.get("kind")
    if kind not in SOURCE_KINDS:
        log.warning(
            "settings: invalid source.kind %r (expected one of %s) "
            "— resetting source to defaults",
            kind,
            SOURCE_KINDS,
        )
        return SourceConfig()
    spec = raw.get("spec")
    if spec is not None and not isinstance(spec, str):
        log.warning("settings: source.spec is not a string; ignoring")
        spec = None
    extras = raw.get("extras") or []
    if not (isinstance(extras, list) and all(isinstance(e, str) for e in extras)):
        log.warning("settings: source.extras is not a list[str]; ignoring")
        extras = []
    return SourceConfig(kind=kind, spec=spec, extras=list(extras))


def _coerce_update(raw: Any, log) -> UpdateConfig:
    if not isinstance(raw, dict):
        log.warning("settings: update is not a dict; resetting to defaults")
        return UpdateConfig()
    mode = raw.get("mode")
    if mode not in UPDATE_MODES:
        log.warning(
            "settings: invalid update.mode %r (expected one of %s) "
            "— resetting update to defaults",
            mode,
            UPDATE_MODES,
        )
        return UpdateConfig()
    hours = raw.get("check-cache-hours", DEFAULT_CHECK_CACHE_HOURS)
    if not (isinstance(hours, int) and hours > 0):
        log.warning(
            "settings: update.check-cache-hours must be a positive int; "
            "using default %d",
            DEFAULT_CHECK_CACHE_HOURS,
        )
        hours = DEFAULT_CHECK_CACHE_HOURS
    return UpdateConfig(mode=mode, check_cache_hours=hours)


def _coerce_runtime(raw: Any, log) -> RuntimeConfig:
    if not isinstance(raw, dict):
        log.warning("settings: runtime is not a dict; resetting to defaults")
        return RuntimeConfig()
    cfg = RuntimeConfig()
    venv_path = raw.get("venv-path")
    if venv_path is not None and isinstance(venv_path, str):
        cfg.venv_path = venv_path
    last_ver = raw.get("last-installed-version")
    if last_ver is not None and isinstance(last_ver, str):
        cfg.last_installed_version = last_ver
    last_check = raw.get("last-check-at")
    if last_check is not None and isinstance(last_check, str):
        cfg.last_check_at = last_check
    return cfg


def _to_json(s: AppSettings) -> dict[str, Any]:
    """JSON-friendly dict using the canonical hyphenated key names."""
    src = asdict(s.source)
    upd = {"mode": s.update.mode, "check-cache-hours": s.update.check_cache_hours}
    rt = {
        "venv-path": s.runtime.venv_path,
        "last-installed-version": s.runtime.last_installed_version,
        "last-check-at": s.runtime.last_check_at,
    }
    return {"source": src, "update": upd, "runtime": rt}


def _populate_runtime_defaults(s: AppSettings) -> None:
    """Fill ``runtime.venv_path`` from the canonical location if unset."""
    if not s.runtime.venv_path:
        s.runtime.venv_path = str(venv_dir())


def load() -> AppSettings:
    """Read settings, creating defaults if missing or invalid."""
    log = get_logger()
    path = settings_path()
    if not path.is_file():
        log.info("settings: creating defaults at %s", path)
        s = AppSettings()
        _populate_runtime_defaults(s)
        save(s)
        return s
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("settings: failed to parse %s (%s) — using defaults", path, e)
        s = AppSettings()
        _populate_runtime_defaults(s)
        return s
    if not isinstance(raw, dict):
        log.warning("settings: top-level is not a mapping; using defaults")
        s = AppSettings()
        _populate_runtime_defaults(s)
        return s
    s = AppSettings(
        source=_coerce_source(raw.get("source") or {}, log),
        update=_coerce_update(raw.get("update") or {}, log),
        runtime=_coerce_runtime(raw.get("runtime") or {}, log),
    )
    _populate_runtime_defaults(s)
    return s


def save(s: AppSettings) -> None:
    """Atomically write settings to disk (write+rename)."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(_to_json(s), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def reset() -> AppSettings:
    """Overwrite settings file with defaults; return the new value."""
    s = AppSettings()
    save(s)
    return s


__all__ = [
    "SOURCE_KINDS",
    "UPDATE_MODES",
    "SourceConfig",
    "UpdateConfig",
    "RuntimeConfig",
    "AppSettings",
    "load",
    "save",
    "reset",
]
