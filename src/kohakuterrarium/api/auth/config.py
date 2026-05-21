"""Auth configuration — single ``[auth]`` section across env / file / TOML.

The config is read fresh on each :func:`load_auth_config` call so tests
that flip env vars between cases see the change without restarting the
process.  Operators get three input shapes:

1. **Env vars** — ``KT_AUTH_HOST_TOKEN``, ``KT_AUTH_ADMIN_TOKEN``,
   ``KT_AUTH_MULTI_USER``, ``KT_AUTH_REGISTRATION``,
   ``KT_AUTH_LOOPBACK_BYPASS``, ``KT_AUTH_SESSION_EXPIRE_HOURS``,
   ``KT_AUTH_BCRYPT_ROUNDS``.
2. **Secret files** — ``KT_AUTH_HOST_TOKEN_FILE`` / ``KT_AUTH_ADMIN_TOKEN_FILE``
   point at a file whose first line is the secret.  Used by Docker
   ``secrets:`` and systemd ``LoadCredential=`` so secrets never appear
   in ``/proc/<pid>/environ``.
3. **TOML** — the ``[auth]`` section of ``<config_dir>/config.toml``.

Precedence: env var > ``*_FILE`` > TOML > default.  Each layer is
applied in order; a higher-precedence layer overrides only the keys it
sets.

Empty-string tokens mean "off" — the gate skips entirely.  This keeps
existing operators' deployments working bit-for-bit when they upgrade
without setting any new config.
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
    """Frozen snapshot of the four-layer auth configuration.

    Frozen so two calls in the same request observe identical values
    even if env vars change mid-request — every entry point reads
    fresh via :func:`load_auth_config` and passes the snapshot down.
    """

    host_token: str = ""
    """L2 — bearer token gating every ``/api/*`` and ``/ws/*`` request.  Empty = off."""

    admin_token: str = ""
    """L3 — ``X-Admin-Token`` header gating config-mutation routes.  Empty = off."""

    multi_user: str = "off"
    """L4 — ``off`` | ``optional`` | ``required``.

    - ``off``: anonymous one-shared-engine mode (current behaviour).
    - ``optional``: anonymous reads allowed; authenticated callers get
      per-user routing.
    - ``required``: routes that hand out a per-user engine
      (every ``Depends(get_service)`` consumer — chat, sessions,
      runtime ops) require a user identity.  **Not** a blanket
      "every ``/api/*`` needs a user": shared-resource reads
      (catalogs, installed packages, LLM model lists, capabilities)
      remain reachable to anyone past L2 because they're host-wide
      configuration, not user data.  See the authentication guide
      §"What 'required' gates" for the exact route catalogue.

      If you need the stricter "every route needs login" posture,
      front the host with a reverse proxy enforcing HTTP basic auth
      on top — that's the conventional way to layer two access
      policies for container deployments.
    """

    registration: str = "admin_only"
    """``open`` | ``invite_only`` | ``admin_only``.

    Only meaningful when ``multi_user != "off"``.  ``open`` means
    anyone can POST ``/auth/register``; ``invite_only`` requires a
    valid invitation token; ``admin_only`` rejects self-registration
    (operator runs ``kt admin users add`` instead).
    """

    loopback_bypass: bool = True
    """When true, requests from ``127.0.0.1`` / ``::1`` skip L2 only.

    L3 and L4 are NOT bypassed — those gate semantics matter even on
    loopback (an attacker running code in the same UID shouldn't be
    able to silently change LLM configs).
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
        """Shape used by the ``/api/auth/capabilities`` response.

        Carries NO secrets — only the enabled flag + mode metadata so
        the frontend can decide what to prompt for.
        """
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


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _read_secret_file(path_str: str) -> str:
    """Read the first line of a secret file; empty string on any error.

    Trailing whitespace / newlines are stripped — common when secrets
    are echoed into the file by deployment tooling.  Read errors log a
    warning and return ``""`` (gate stays off) rather than raising —
    boot must not fail on a missing-but-optional secret.
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
    # First line, stripped — multi-line files take the first non-empty line.
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
    """Resolve a secret with documented precedence.

    Audit fix — the prior "or"-chained fallback let
    ``KT_AUTH_HOST_TOKEN=""`` (explicit empty) silently fall through
    to a TOML token, which surprises operators who set the env to
    disable the gate.  Now: if ``env_var`` is **present** in the
    environment, its value wins (even when empty).  Only an
    *unset* env var falls through to the file / TOML chain.
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
    """Read env + secret files + TOML and freeze into an :class:`AuthConfig`.

    Precedence per field (highest wins): env var > ``*_FILE`` > TOML >
    dataclass default.  Logs a single line at INFO with the resolved
    summary on each call — useful for boot diagnostics but not noisy
    enough to spam request paths (the middleware caches via
    ``app.state.auth_config`` in real serving).
    """
    toml_section = _read_toml_auth_section()
    defaults = AuthConfig()

    # Tokens — explicit env override semantics:
    #   1. ``KT_AUTH_*_TOKEN`` set (even to empty string) → use env value
    #      verbatim.  The audit caught this case: an operator who sets
    #      ``KT_AUTH_HOST_TOKEN=""`` expects the gate OFF, but the old
    #      fall-through logic would silently revive the TOML value.
    #   2. ``KT_AUTH_*_TOKEN_FILE`` set → read the file's first line.
    #   3. TOML ``[auth] host_token`` → use it.
    #   4. Default ("" = off).
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

    # Modes — env wins, else TOML, else default.
    multi_user_raw = os.environ.get(
        "KT_AUTH_MULTI_USER", toml_section.get("multi_user", defaults.multi_user)
    )
    multi_user = _validate_multi_user(multi_user_raw, defaults.multi_user)

    registration_raw = os.environ.get(
        "KT_AUTH_REGISTRATION",
        toml_section.get("registration", defaults.registration),
    )
    registration = _validate_registration(registration_raw, defaults.registration)

    # Bools / ints.
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
