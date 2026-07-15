"""Resolve authentication policy from environment, secret files, and TOML.

Each load returns a fresh immutable snapshot. Field precedence is environment,
secret file, TOML, then default; an explicitly empty token disables its gate and
must not fall through to a lower-precedence secret.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_VALID_MULTI_USER_MODES: frozenset[str] = frozenset({"off", "optional", "required"})
_VALID_REGISTRATION_MODES: frozenset[str] = frozenset(
    {"open", "invite_only", "admin_only"}
)


@dataclass(frozen=True)
class AuthConfig:
    """Immutable authentication policy shared throughout one request or app."""

    host_token: str = ""
    """L2 — bearer token gating every ``/api/*`` and ``/ws/*`` request.  Empty = off."""

    admin_token: str = ""
    """L3 — ``X-Admin-Token`` header gating config-mutation routes.  Empty = off."""

    multi_user: str = "off"
    """User-isolation mode: ``off``, ``optional``, or ``required``.

    Required mode protects routes that resolve user-scoped engines and sessions;
    host-wide catalog and configuration reads remain governed by the host-token
    layer rather than user identity.
    """

    registration: str = "admin_only"
    """Registration policy used only when user isolation is enabled.

    Open registration accepts self-service sign-up, invite-only requires a valid
    invitation, and admin-only disables the public registration path.
    """

    loopback_bypass: bool = True
    """Allow loopback requests to bypass only the host-token gate.

    Administrative and user-identity checks still apply on loopback because local
    processes must not gain configuration or user-data privileges implicitly.
    """

    session_expire_hours: int = 168
    """L4 cookie / DB session lifetime.  Default 7 days."""

    session_idle_minutes: int = 0
    """L4 idle expiry (last_seen).  ``0`` = no idle expiry."""

    bcrypt_rounds: int = 12
    """Password hash work factor.  12 is bcrypt's modern recommendation."""

    @property
    def host_token_enabled(self) -> bool:
        return bool(self.host_token)

    @property
    def admin_token_enabled(self) -> bool:
        return bool(self.admin_token)

    @property
    def multi_user_enabled(self) -> bool:
        return self.multi_user != "off"

    def as_capabilities_dict(self) -> dict[str, dict[str, object]]:
        """Return non-secret policy metadata for frontend authentication prompts."""
        return {
            "host_token": {
                "enabled": self.host_token_enabled,
                "loopback_bypass": self.loopback_bypass,
            },
            "admin_token": {"enabled": self.admin_token_enabled},
            "multi_user": {
                "enabled": self.multi_user_enabled,
                "mode": self.multi_user,
                "registration": self.registration,
            },
        }


# Configuration source readers and coercion rules.


def _read_secret_file(path_str: str) -> str:
    """Return the first non-empty secret line, or disable the gate on read error.

    Deployment tooling commonly adds surrounding whitespace, which is stripped.
    Optional secret-file failures are logged rather than preventing server startup.
    """
    if not path_str:
        return ""
    try:
        text = Path(path_str).read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(
            "auth: secret file unreadable, treating as unset",
            path=path_str,
            error=str(e),
        )
        return ""
    # Credential files may include blank framing lines; the first value is authoritative.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _read_toml_auth_section() -> dict[str, object]:
    """Read the ``[auth]`` table from ``<config_dir>/config.toml``."""
    path = config_dir() / "config.toml"
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(
            "auth: config.toml unreadable / malformed; ignoring",
            path=str(path),
            error=str(e),
        )
        return {}
    section = data.get("auth")
    if not isinstance(section, dict):
        return {}
    return section


def _coerce_bool(value: object, default: bool) -> bool:
    """Permissive bool coercion for env vars + TOML."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _coerce_int(value: object, default: int) -> int:
    """Permissive int coercion; falls back to default on parse error."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _resolve_secret(*, env_var: str, env_file_var: str, toml_value: object) -> str:
    """Resolve a secret while preserving explicit empty environment values.

    Presence and truthiness are intentionally distinct: an empty environment value
    disables the gate instead of reviving a file or TOML secret.
    """
    if env_var in os.environ:
        return os.environ[env_var].strip()
    file_path = os.environ.get(env_file_var, "")
    if file_path:
        return _read_secret_file(file_path)
    return str(toml_value or "").strip()


def _validate_multi_user(value: object, default: str) -> str:
    if isinstance(value, str) and value in _VALID_MULTI_USER_MODES:
        return value
    if value is not None and value != "":
        logger.warning(
            "auth: invalid multi_user value, falling back to default",
            value=str(value),
            default=default,
        )
    return default


def _validate_registration(value: object, default: str) -> str:
    if isinstance(value, str) and value in _VALID_REGISTRATION_MODES:
        return value
    if value is not None and value != "":
        logger.warning(
            "auth: invalid registration value, falling back to default",
            value=str(value),
            default=default,
        )
    return default


def load_auth_config() -> AuthConfig:
    """Load and validate an immutable authentication configuration snapshot."""
    toml_section = _read_toml_auth_section()
    defaults = AuthConfig()

    # Token precedence is environment presence, secret file, TOML, then disabled.
    # Empty environment values are authoritative because they intentionally turn gates off.
    host_token = _resolve_secret(
        env_var="KT_AUTH_HOST_TOKEN",
        env_file_var="KT_AUTH_HOST_TOKEN_FILE",
        toml_value=toml_section.get("host_token", ""),
    )
    admin_token = _resolve_secret(
        env_var="KT_AUTH_ADMIN_TOKEN",
        env_file_var="KT_AUTH_ADMIN_TOKEN_FILE",
        toml_value=toml_section.get("admin_token", ""),
    )

    # Enumerated modes use environment, TOML, then validated defaults.
    multi_user_raw = os.environ.get(
        "KT_AUTH_MULTI_USER", toml_section.get("multi_user", defaults.multi_user)
    )
    multi_user = _validate_multi_user(multi_user_raw, defaults.multi_user)

    registration_raw = os.environ.get(
        "KT_AUTH_REGISTRATION",
        toml_section.get("registration", defaults.registration),
    )
    registration = _validate_registration(registration_raw, defaults.registration)

    # Scalar policy values tolerate common environment and TOML representations.
    loopback_bypass = _coerce_bool(
        os.environ.get(
            "KT_AUTH_LOOPBACK_BYPASS",
            toml_section.get("loopback_bypass", defaults.loopback_bypass),
        ),
        defaults.loopback_bypass,
    )
    session_expire_hours = _coerce_int(
        os.environ.get(
            "KT_AUTH_SESSION_EXPIRE_HOURS",
            toml_section.get("session_expire_hours", defaults.session_expire_hours),
        ),
        defaults.session_expire_hours,
    )
    session_idle_minutes = _coerce_int(
        os.environ.get(
            "KT_AUTH_SESSION_IDLE_MINUTES",
            toml_section.get("session_idle_minutes", defaults.session_idle_minutes),
        ),
        defaults.session_idle_minutes,
    )
    bcrypt_rounds = _coerce_int(
        os.environ.get(
            "KT_AUTH_BCRYPT_ROUNDS",
            toml_section.get("bcrypt_rounds", defaults.bcrypt_rounds),
        ),
        defaults.bcrypt_rounds,
    )

    cfg = AuthConfig(
        host_token=host_token,
        admin_token=admin_token,
        multi_user=multi_user,
        registration=registration,
        loopback_bypass=loopback_bypass,
        session_expire_hours=session_expire_hours,
        session_idle_minutes=session_idle_minutes,
        bcrypt_rounds=bcrypt_rounds,
    )
    return cfg


__all__ = ["AuthConfig", "load_auth_config"]
