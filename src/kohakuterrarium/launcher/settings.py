"""``app-settings.json`` read / write / validate.

Schema lives in this module so the launcher can validate without
pulling pydantic. Invalid fields fall back to defaults with a one-line
warning so a hand-edited file never wedges the wrapper on a parse
error.

Settings shape (canonical: ``plans/1.5.0-roadmap/06b-release-bundle-update/design.md`` §3):

.. code-block:: json

    {
      "feed": {
        "kind": "github_releases" | "custom",
        "repo": "Kohaku-Lab/KohakuTerrarium",
        "url": null | "https://..."
      },
      "channel": "stable" | "beta" | "nightly",
      "pinned_version": null | "1.5.1",
      "update": {
        "mode": "manual" | "notify-on-launch" | "auto-on-launch",
        "check-cache-hours": 24,
        "keep-versions": 3
      },
      "runtime": {
        "active-version": null | "1.5.1",
        "active-build-id": null | "20260519-153000-abc1234",
        "last-check-at": null | "<iso8601>",
        "last-check-error": null | "<message>"
      }
    }

Legacy 06 settings (``source`` block + ``runtime.venv-path``) are
silently ignored — the loader resets to defaults if the new keys are
missing.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import settings_path

FEED_KINDS = ("github_releases", "custom")
CHANNELS = ("stable", "beta", "nightly")
UPDATE_MODES = ("manual", "notify-on-launch", "auto-on-launch")

DEFAULT_REPO = "Kohaku-Lab/KohakuTerrarium"
DEFAULT_CHECK_CACHE_HOURS = 24
DEFAULT_KEEP_VERSIONS = 3


@dataclass
class FeedConfig:
    kind: str = "github_releases"
    repo: str = DEFAULT_REPO
    url: str | None = None


@dataclass
class UpdateConfig:
    mode: str = "notify-on-launch"
    check_cache_hours: int = DEFAULT_CHECK_CACHE_HOURS
    keep_versions: int = DEFAULT_KEEP_VERSIONS


@dataclass
class RuntimeConfig:
    active_version: str | None = None
    active_build_id: str | None = None
    last_check_at: str | None = None
    last_check_error: str | None = None


@dataclass
class AppSettings:
    feed: FeedConfig = field(default_factory=FeedConfig)
    channel: str = "stable"
    pinned_version: str | None = None
    update: UpdateConfig = field(default_factory=UpdateConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


# ── Coercion helpers ────────────────────────────────────────────────


def _coerce_feed(raw: Any, log) -> FeedConfig:
    if not isinstance(raw, dict):
        log.warning("settings: feed is not a dict; resetting to defaults")
        return FeedConfig()
    kind = raw.get("kind")
    if kind not in FEED_KINDS:
        log.warning(
            "settings: invalid feed.kind %r (expected one of %s) — resetting feed",
            kind,
            FEED_KINDS,
        )
        return FeedConfig()
    repo = raw.get("repo") or DEFAULT_REPO
    if not isinstance(repo, str):
        log.warning("settings: feed.repo is not a string; using default")
        repo = DEFAULT_REPO
    url = raw.get("url")
    if url is not None and not isinstance(url, str):
        log.warning("settings: feed.url is not a string; ignoring")
        url = None
    if url is not None and not url.startswith("https://"):
        log.warning("settings: feed.url must be https://; ignoring %r", url)
        url = None
    if kind == "custom" and not url:
        log.warning(
            "settings: feed.kind=custom requires feed.url; resetting to github_releases"
        )
        return FeedConfig()
    return FeedConfig(kind=kind, repo=repo, url=url)


def _coerce_channel(raw: Any, log) -> str:
    if raw in CHANNELS:
        return raw
    if raw is not None:
        log.warning(
            "settings: invalid channel %r (expected one of %s); using stable",
            raw,
            CHANNELS,
        )
    return "stable"


def _coerce_pinned(raw: Any, log) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        log.warning("settings: pinned_version must be a non-empty string; ignoring")
        return None
    return raw.strip()


def _coerce_update(raw: Any, log) -> UpdateConfig:
    if not isinstance(raw, dict):
        log.warning("settings: update is not a dict; resetting to defaults")
        return UpdateConfig()
    mode = raw.get("mode")
    if mode not in UPDATE_MODES:
        log.warning(
            "settings: invalid update.mode %r (expected one of %s); resetting",
            mode,
            UPDATE_MODES,
        )
        return UpdateConfig()
    hours = raw.get("check-cache-hours", DEFAULT_CHECK_CACHE_HOURS)
    if not (isinstance(hours, int) and hours > 0):
        log.warning(
            "settings: update.check-cache-hours must be positive int; using default %d",
            DEFAULT_CHECK_CACHE_HOURS,
        )
        hours = DEFAULT_CHECK_CACHE_HOURS
    keep = raw.get("keep-versions", DEFAULT_KEEP_VERSIONS)
    if not (isinstance(keep, int) and keep > 0):
        log.warning(
            "settings: update.keep-versions must be positive int; using default %d",
            DEFAULT_KEEP_VERSIONS,
        )
        keep = DEFAULT_KEEP_VERSIONS
    return UpdateConfig(mode=mode, check_cache_hours=hours, keep_versions=keep)


def _coerce_runtime(raw: Any, log) -> RuntimeConfig:
    if not isinstance(raw, dict):
        log.warning("settings: runtime is not a dict; resetting to defaults")
        return RuntimeConfig()
    cfg = RuntimeConfig()
    for src_key, attr in (
        ("active-version", "active_version"),
        ("active-build-id", "active_build_id"),
        ("last-check-at", "last_check_at"),
        ("last-check-error", "last_check_error"),
    ):
        val = raw.get(src_key)
        if val is None:
            continue
        if isinstance(val, str):
            setattr(cfg, attr, val)
    return cfg


def _to_json(s: AppSettings) -> dict[str, Any]:
    feed = {"kind": s.feed.kind, "repo": s.feed.repo, "url": s.feed.url}
    upd = {
        "mode": s.update.mode,
        "check-cache-hours": s.update.check_cache_hours,
        "keep-versions": s.update.keep_versions,
    }
    rt = {
        "active-version": s.runtime.active_version,
        "active-build-id": s.runtime.active_build_id,
        "last-check-at": s.runtime.last_check_at,
        "last-check-error": s.runtime.last_check_error,
    }
    return {
        "feed": feed,
        "channel": s.channel,
        "pinned_version": s.pinned_version,
        "update": upd,
        "runtime": rt,
    }


# ── Public IO ───────────────────────────────────────────────────────


def load() -> AppSettings:
    """Read settings, creating defaults if missing or invalid.

    Legacy 06 ``source`` blocks are silently dropped — the new schema
    has no equivalent. ``update.mode`` and the cache-hours are
    preserved if valid.
    """
    log = get_logger()
    path = settings_path()
    if not path.is_file():
        log.info("settings: creating defaults at %s", path)
        s = AppSettings()
        save(s)
        return s
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("settings: failed to parse %s (%s) — using defaults", path, e)
        return AppSettings()
    if not isinstance(raw, dict):
        log.warning("settings: top-level is not a mapping; using defaults")
        return AppSettings()
    # Detect legacy 06 shape and log once so support can spot it.
    if "source" in raw and "feed" not in raw:
        log.info(
            "settings: legacy 06 source block detected at %s; ignoring (drop on next save)",
            path,
        )
    return AppSettings(
        feed=_coerce_feed(raw.get("feed") or {}, log),
        channel=_coerce_channel(raw.get("channel"), log),
        pinned_version=_coerce_pinned(raw.get("pinned_version"), log),
        update=_coerce_update(raw.get("update") or {}, log),
        runtime=_coerce_runtime(raw.get("runtime") or {}, log),
    )


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


def to_public_dict(s: AppSettings) -> dict[str, Any]:
    """Same as ``_to_json`` but exposed for the API layer."""
    return _to_json(s)


def from_public_dict(raw: dict[str, Any]) -> AppSettings:
    """Build an ``AppSettings`` from a dict supplied by the API client.

    Runs through the same coercion path as :func:`load` so invalid
    inputs end up as warnings + defaults instead of HTTP 500s.
    """
    log = get_logger()
    if not isinstance(raw, dict):
        return AppSettings()
    return AppSettings(
        feed=_coerce_feed(raw.get("feed") or {}, log),
        channel=_coerce_channel(raw.get("channel"), log),
        pinned_version=_coerce_pinned(raw.get("pinned_version"), log),
        update=_coerce_update(raw.get("update") or {}, log),
        runtime=_coerce_runtime(raw.get("runtime") or {}, log),
    )


__all__ = [
    "FEED_KINDS",
    "CHANNELS",
    "UPDATE_MODES",
    "DEFAULT_REPO",
    "FeedConfig",
    "UpdateConfig",
    "RuntimeConfig",
    "AppSettings",
    "load",
    "save",
    "reset",
    "to_public_dict",
    "from_public_dict",
]
