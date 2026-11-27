"""Named secrets for browser actions: environment variables, allowed origins, redaction.

Options name an environment variable and the web origins each secret may be
typed into; values are read from the environment each time they are needed
and are redacted from text the agent will see.
"""

import json
import os
import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any
from urllib.parse import SplitResult, quote, quote_plus, urlsplit

from kohakuterrarium.browser.policy import normalize_host
from kohakuterrarium.errors import ConfigError, NotFoundError

MIN_REDACT_LENGTH = 4
REDACTED = "[redacted]"
SPEC_KEYS = ("env", "origins")
DEFAULT_PORTS = {"http": 80, "https": 443}
SHAPE = "{env: VAR_NAME, origins: ['https://site.example']}"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_ENV_VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _origin(parts: SplitResult) -> str:
    """``scheme://host[:port]`` of split URL ``parts``; raises ``ValueError``."""
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise ValueError("the scheme must be http or https")
    if not parts.hostname:
        raise ValueError("there is no host")
    host = normalize_host(parts.hostname)
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and port != DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def parse_origin(text: object) -> str:
    """The normalised origin a secret option names: ``scheme://host[:port]``.

    Raises ``ConfigError`` for anything else: a path, query or fragment,
    credentials, a wildcard, or a scheme other than http and https.
    """
    if not isinstance(text, str) or not text.strip():
        raise ConfigError(f"{text!r} is not an origin like 'https://site.example'")
    raw = text.strip()
    if "*" in raw:
        raise ConfigError(f"origin {raw!r}: wildcards are not supported")
    try:
        parts = urlsplit(raw)
        if parts.path not in ("", "/") or "?" in raw or "#" in raw:
            raise ValueError("give only scheme, host and port, without a path")
        if parts.username is not None or parts.password is not None:
            raise ValueError("credentials are not allowed")
        return _origin(parts)
    except ValueError as exc:
        raise ConfigError(f"origin {raw!r}: {exc}") from None


def web_origin(url: str) -> str | None:
    """The normalised origin of an http(s) page URL; ``None`` for any other URL."""
    try:
        return _origin(urlsplit(url))
    except ValueError:
        return None


def _spec(name: str, value: object) -> tuple[str, tuple[str, ...]]:
    """``(variable, origins)`` of one secret's option value, or ``ConfigError``."""
    if not isinstance(value, Mapping):
        raise ConfigError(f"secret {name!r} must be a mapping {SHAPE}")
    extra = sorted(str(key) for key in value if key not in SPEC_KEYS)
    if extra:
        raise ConfigError(f"secret {name!r}: unknown key(s) {', '.join(extra)}")
    variable = value.get("env")
    if not isinstance(variable, str) or not _ENV_VAR.fullmatch(variable.strip()):
        raise ConfigError(
            f"secret {name!r}: env must name an environment variable, like SHOP_PW"
        )
    origins = value.get("origins")
    if not isinstance(origins, (list, tuple)) or not origins:
        raise ConfigError(
            f"secret {name!r}: origins must be a non-empty list of web origins "
            "it may be typed into, like ['https://site.example']"
        )
    parsed: list[str] = []
    for entry in origins:
        try:
            origin = parse_origin(entry)
        except ConfigError as exc:
            raise ConfigError(f"secret {name!r}: {exc}") from None
        if origin not in parsed:
            parsed.append(origin)
    return variable.strip(), tuple(parsed)


def parse_secrets(value: object) -> Mapping[str, Mapping[str, Any]]:
    """Validated, normalised, read-only secret options; raises ``ConfigError``."""
    if not isinstance(value, Mapping):
        raise ConfigError(f"secrets must map secret names to {SHAPE}")
    parsed: dict[str, Mapping[str, Any]] = {}
    for name, spec in value.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ConfigError(f"invalid secret name {name!r}")
        variable, origins = _spec(name, spec)
        parsed[name] = MappingProxyType({"env": variable, "origins": origins})
    return MappingProxyType(parsed)


def value_forms(value: str) -> tuple[str, ...]:
    """The raw value plus its distinct encoded spellings.

    Covers URL encoding (path and form), JSON / double-quoted YAML string
    escaping and single-quoted YAML quote doubling.
    """
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    spellings = (
        value,
        quote(value, safe=""),
        quote_plus(value),
        escaped,
        json.dumps(value)[1:-1],
        value.replace("'", "''"),
    )
    forms: list[str] = []
    for form in spellings:
        if form not in forms:
            forms.append(form)
    return tuple(forms)


class SecretStore:
    """Secret names to environment variables and allowed origins, resolved at call time."""

    def __init__(self, specs: Mapping[str, Any]):
        parsed = parse_secrets(specs)
        self._variables = {name: spec["env"] for name, spec in parsed.items()}
        self._origins = {name: spec["origins"] for name, spec in parsed.items()}

    @property
    def names(self) -> tuple[str, ...]:
        """The configured secret names."""
        return tuple(self._variables)

    def __contains__(self, name: object) -> bool:
        return name in self._variables

    def origins(self, name: str) -> tuple[str, ...]:
        """The origins secret ``name`` may be typed into; empty for an unknown name."""
        return self._origins.get(name, ())

    def allows(self, name: str, origin: str | None) -> bool:
        """Whether secret ``name`` may be typed into a document of ``origin``."""
        return origin is not None and origin in self.origins(name)

    def resolve(self, name: str) -> str:
        """The current value of secret ``name``; errors name the secret, never a value."""
        variable = self._variables.get(name)
        if variable is None:
            raise NotFoundError(f"unknown secret {name!r}")
        value = os.environ.get(variable)
        if not value:
            raise ConfigError(
                f"secret {name!r} is unavailable: environment variable "
                f"{variable} is unset or empty"
            )
        if len(value) < MIN_REDACT_LENGTH:
            raise ConfigError(
                f"secret {name!r} is shorter than {MIN_REDACT_LENGTH} characters "
                "and could not be redacted"
            )
        return value

    def redact(self, text: str, extra: Iterable[str] = ()) -> str:
        """Replace secret values with ``[secret:<name>]`` and ``extra`` values with ``[redacted]``.

        Raw and URL-encoded spellings are both replaced. Values shorter than
        ``MIN_REDACT_LENGTH`` are left alone; longer values win where values
        overlap.
        """
        labels: dict[str, str] = {}
        named = [
            (os.environ.get(variable), f"[secret:{name}]")
            for name, variable in self._variables.items()
        ]
        for value, label in named + [(value, REDACTED) for value in extra]:
            if value and len(value) >= MIN_REDACT_LENGTH:
                for form in value_forms(value):
                    labels.setdefault(form, label)
        if not labels or not text:
            return text
        ordered = sorted(labels, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(value) for value in ordered))
        return pattern.sub(lambda match: labels[match.group(0)], text)
