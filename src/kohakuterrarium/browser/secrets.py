"""Named secrets for browser actions: ``env:`` references, resolution, redaction.

Options hold references only; values are read from the environment each
time they are needed and are redacted from text the agent will see.
"""

import os
import re
from collections.abc import Iterable, Mapping
from urllib.parse import quote, quote_plus

from kohakuterrarium.errors import ConfigError, NotFoundError

REFERENCE_PREFIX = "env:"
MIN_REDACT_LENGTH = 4
REDACTED = "[redacted]"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_ENV_VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def value_forms(value: str) -> tuple[str, ...]:
    """The raw value plus its distinct URL-encoded spellings (path and form encoding)."""
    forms: list[str] = []
    for form in (value, quote(value, safe=""), quote_plus(value)):
        if form not in forms:
            forms.append(form)
    return tuple(forms)


def parse_reference(reference: object) -> str:
    """Return the variable name of an ``env:VAR_NAME`` reference, or raise ``ConfigError``."""
    if not isinstance(reference, str):
        raise ConfigError("a secret reference must be a string like 'env:VAR_NAME'")
    text = reference.strip()
    variable = text[len(REFERENCE_PREFIX) :]
    if not text.startswith(REFERENCE_PREFIX) or not _ENV_VAR.fullmatch(variable):
        raise ConfigError("a secret reference must have the form 'env:VAR_NAME'")
    return variable


class SecretStore:
    """Secret name to environment variable mapping, resolved at call time."""

    def __init__(self, references: Mapping[str, str]):
        variables: dict[str, str] = {}
        for name, reference in references.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ConfigError(f"invalid secret name {name!r}")
            try:
                variables[name] = parse_reference(reference)
            except ConfigError as exc:
                raise ConfigError(f"secret {name!r}: {exc}") from None
        self._variables = variables

    @property
    def names(self) -> tuple[str, ...]:
        """The configured secret names."""
        return tuple(self._variables)

    def __contains__(self, name: object) -> bool:
        return name in self._variables

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
