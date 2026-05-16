"""Resolve the per-user config directory.

Single source of truth: ``KT_CONFIG_DIR`` env var overrides the
default ``~/.kohakuterrarium``.  Every caller that needs to read
or write under the user's config root should go through
:func:`config_dir` so:

1. Tests get isolation via ``monkeypatch.setenv("KT_CONFIG_DIR", ...)``
   without touching the real user state on disk.
2. Operators can re-home the config tree (e.g. into a managed
   network share) without code changes.

The directory is created on first read so callers can safely build
paths under it.
"""

import os
from pathlib import Path

_DEFAULT = "~/.kohakuterrarium"


def config_dir() -> Path:
    """Return the user config directory.

    Honors the ``KT_CONFIG_DIR`` environment variable; falls back to
    ``~/.kohakuterrarium``.  Always returns an absolute :class:`Path`;
    the directory is created if it doesn't exist.
    """
    raw = os.environ.get("KT_CONFIG_DIR") or _DEFAULT
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_subdir(*parts: str) -> Path:
    """Return ``config_dir() / parts`` with the path created."""
    path = config_dir().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["config_dir", "config_subdir"]
