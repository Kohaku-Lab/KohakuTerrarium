"""Bring a session file from elsewhere on disk into the session folder."""

import sqlite3
from pathlib import Path

from kohakuterrarium.studio.persistence.viewer.paths import (
    normalize_session_stem,
    resolve_session_path,
)

SESSION_SUFFIX = ".kohakutr"


class SessionImportError(ValueError):
    """The file cannot be imported as a session."""


def _free_name(stem: str, session_dir: Path) -> str:
    name, number = stem, 1
    while (
        resolve_session_path(name, session_dir) is not None
        or (session_dir / f"{name}{SESSION_SUFFIX}").exists()
    ):
        number += 1
        name = f"{stem}-{number}"
    return name


def import_session_file(source: Path, session_dir: Path) -> str:
    """Copy ``source`` into ``session_dir`` and return the session's name.

    A file already inside ``session_dir`` is left where it is. The copy goes
    through SQLite's backup API, so a session that still has write-ahead data
    arrives complete and a file that is not a database is refused.
    """
    source = source.resolve()
    if source.suffix != SESSION_SUFFIX or not source.is_file():
        raise SessionImportError(f"{source.name} is not a {SESSION_SUFFIX} file")
    if source.parent == session_dir.resolve():
        return normalize_session_stem(source)

    session_dir.mkdir(parents=True, exist_ok=True)
    name = _free_name(normalize_session_stem(source), session_dir)
    target = session_dir / f"{name}{SESSION_SUFFIX}"
    reader = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    writer = sqlite3.connect(target)
    try:
        reader.backup(writer)
    except sqlite3.DatabaseError as exc:
        writer.close()
        target.unlink(missing_ok=True)
        raise SessionImportError(f"{source.name} is not a session file") from exc
    finally:
        reader.close()
        writer.close()
    return name
