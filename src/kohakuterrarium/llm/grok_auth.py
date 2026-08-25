"""Load reusable Grok subscription access tokens from local applications.

KT never consumes or writes third-party refresh tokens.  When a Grok CLI access
token is nearly expired, KT asks the owning CLI to refresh its own credential and
then rereads the access token written by the CLI.
"""

import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

GROK_CLI_SOURCE = "grok-cli"
OPENCODE_SOURCE = "opencode"
XAI_BASE_URL = "https://api.x.ai/v1"
GROK_CLI_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
_EXPIRY_SKEW_SECONDS = 30
_GROK_CLI_REFRESH_WINDOW_SECONDS = 30 * 60
_GROK_CLI_REFRESH_TIMEOUT_SECONDS = 20.0
_refresh_tasks: dict[asyncio.AbstractEventLoop, asyncio.Task["GrokToken | None"]] = {}
_GROK_VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)*(?: \([0-9A-Fa-f]+\))?$"
)


@dataclass(frozen=True)
class GrokToken:
    """A borrowed access token and the request profile required by its owner."""

    access_token: str = field(repr=False)
    source: str
    expires_at: float | None = None
    base_url: str = XAI_BASE_URL
    media_base_url: str = XAI_BASE_URL
    extra_headers: dict[str, str] = field(default_factory=dict)

    def is_expired(self, now: float | None = None) -> bool:
        """Return whether the token is expired or too close to expiry to use."""
        if self.expires_at is None:
            return False
        current = time.time() if now is None else now
        return self.expires_at <= current + _EXPIRY_SKEW_SECONDS


class GrokTokens:
    """Discover usable Grok subscription tokens in a fixed, safe order."""

    @classmethod
    def load_candidates(cls) -> list[GrokToken]:
        """Return valid Grok CLI then OpenCode access-token candidates."""
        candidates = [
            _load_grok_cli_token(),
            _load_opencode_token(),
        ]
        return [token for token in candidates if token and not token.is_expired()]

    @classmethod
    def load_bootstrap_candidates(cls) -> list[GrokToken]:
        """Return candidates usable to construct a provider before CLI refresh."""
        candidates = cls.load_candidates()
        if candidates:
            return candidates
        token = _load_grok_cli_token()
        return [token] if token and _grok_cli_executable() else []

    @classmethod
    def available(cls) -> bool:
        """Return whether a valid or CLI-refreshable local login exists."""
        return bool(cls.load_bootstrap_candidates())

    @classmethod
    async def ensure_fresh_cli(cls, *, force: bool = False) -> GrokToken | None:
        """Ask Grok CLI to refresh its token when due, sharing concurrent work."""
        current = _load_grok_cli_token()
        if current is None:
            return None
        if not force and not _needs_cli_refresh(current):
            return current

        loop = asyncio.get_running_loop()
        task = _refresh_tasks.get(loop)
        if task is None or task.done():
            task = loop.create_task(_refresh_grok_cli(current, force=force))
            _refresh_tasks[loop] = task

            def clear(done: asyncio.Task[GrokToken | None]) -> None:
                if _refresh_tasks.get(loop) is done:
                    _refresh_tasks.pop(loop, None)

            task.add_done_callback(clear)
        return await asyncio.shield(task)


def _grok_home() -> Path:
    return Path(os.environ.get("GROK_HOME", "~/.grok")).expanduser()


def _grok_auth_path() -> Path:
    return _grok_home() / "auth.json"


def _opencode_auth_path() -> Path:
    override = os.environ.get("OPENCODE_AUTH_FILE")
    if override:
        return Path(override).expanduser()
    return Path("~/.local/share/opencode/auth.json").expanduser()


def _load_grok_cli_token() -> GrokToken | None:
    data = _read_json(_grok_auth_path(), GROK_CLI_SOURCE)
    if not isinstance(data, dict):
        return None

    entries = _candidate_dicts(data)
    for entry in entries:
        access = _first_text(entry, "key", "access_token", "access", "token")
        if not access:
            continue
        token = GrokToken(
            access_token=access,
            source=GROK_CLI_SOURCE,
            expires_at=_expiry_value(entry),
            base_url=GROK_CLI_BASE_URL,
            extra_headers=_grok_cli_headers(),
        )
        return token
    return None


def _needs_cli_refresh(token: GrokToken, now: float | None = None) -> bool:
    if token.expires_at is None:
        return False
    current = time.time() if now is None else now
    return token.expires_at <= current + _GROK_CLI_REFRESH_WINDOW_SECONDS


async def _refresh_grok_cli(before: GrokToken, *, force: bool) -> GrokToken | None:
    await _run_grok_models()
    refreshed = _load_grok_cli_token()
    if refreshed is None or _needs_cli_refresh(refreshed):
        return None
    if force and refreshed.access_token == before.access_token:
        return None
    return refreshed


async def _run_grok_models() -> bool:
    """Run a non-generating CLI command; auth-file changes prove refresh."""
    executable = _grok_cli_executable()
    if executable is None:
        return False
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "models",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(
                process.communicate(), timeout=_GROK_CLI_REFRESH_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
            return False
    except (OSError, asyncio.SubprocessError) as exc:
        logger.warning("Grok CLI credential refresh failed", error=type(exc).__name__)
        return False
    return process.returncode == 0


def _grok_cli_executable() -> str | None:
    executable = shutil.which("grok")
    if executable:
        return executable
    bundled = _grok_home() / "bin" / "grok"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return None


def _grok_cli_headers() -> dict[str, str]:
    headers = {
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-authenticateresponse": "authenticate-response",
        "x-grok-client-identifier": "grok-shell",
        "x-grok-client-mode": "interactive",
    }
    version = _read_grok_cli_version()
    if version:
        headers["x-grok-client-version"] = version
    return headers


def _read_grok_cli_version() -> str:
    path = _grok_home() / ".metadata_version"
    try:
        version = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning(
            "Failed to read Grok CLI version metadata",
            path=str(path),
            error=type(exc).__name__,
        )
        return ""
    if not _GROK_VERSION_PATTERN.fullmatch(version):
        logger.warning("Ignoring invalid Grok CLI version metadata", path=str(path))
        return ""
    return version


def _load_opencode_token() -> GrokToken | None:
    data = _read_json(_opencode_auth_path(), OPENCODE_SOURCE)
    if not isinstance(data, dict):
        return None

    raw = data.get("xai")
    if not isinstance(raw, dict):
        return None
    access = _first_text(raw, "access", "access_token", "token", "key")
    if not access:
        return None
    token = GrokToken(
        access_token=access,
        source=OPENCODE_SOURCE,
        expires_at=_expiry_value(raw),
    )
    return None if token.is_expired() else token


def _read_json(path: Path, source: str) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed to read external Grok credential",
            source=source,
            path=str(path),
            error=type(exc).__name__,
        )
        return None
    return data if isinstance(data, dict) else None


def _candidate_dicts(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return root and nested auth entries without depending on map keys."""
    entries = [data]
    entries.extend(value for value in data.values() if isinstance(value, dict))
    return entries


def _first_text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _expiry_value(data: dict[str, Any]) -> float | None:
    value = data.get("expires_at", data.get("expires"))
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            try:
                normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.timestamp()
            except (ValueError, OverflowError):
                return None
    if not isinstance(value, (int, float)):
        return None
    # OpenCode stores milliseconds while OAuth payloads commonly use seconds.
    return float(value / 1000 if value > 10_000_000_000 else value)


__all__ = [
    "GROK_CLI_SOURCE",
    "GROK_CLI_BASE_URL",
    "OPENCODE_SOURCE",
    "XAI_BASE_URL",
    "GrokToken",
    "GrokTokens",
]
