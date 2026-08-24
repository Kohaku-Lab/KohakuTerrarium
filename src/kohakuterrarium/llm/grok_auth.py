"""Load reusable Grok subscription access tokens from local applications.

The owning application remains responsible for refresh and persistence.  KT only
reads access tokens and never consumes or writes third-party refresh tokens.
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

GROK_CLI_SOURCE = "grok-cli"
OPENCODE_SOURCE = "opencode"
XAI_BASE_URL = "https://api.x.ai/v1"
GROK_CLI_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
_EXPIRY_SKEW_SECONDS = 30


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
    def available(cls) -> bool:
        """Return whether at least one reusable local token is currently valid."""
        return bool(cls.load_candidates())


def _grok_auth_path() -> Path:
    root = Path(os.environ.get("GROK_HOME", "~/.grok")).expanduser()
    return root / "auth.json"


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
            extra_headers={"X-XAI-Token-Auth": "xai-grok-cli"},
        )
        if not token.is_expired():
            return token
    return None


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
