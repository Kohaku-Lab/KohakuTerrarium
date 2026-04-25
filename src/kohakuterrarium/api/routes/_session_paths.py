"""Shared session-file path resolution for the session routes.

Factored out of ``api/routes/sessions.py`` so the router file stays
under the 600-line soft cap. Every helper here takes an explicit
``session_dir`` (no module-level state) so tests can monkey-patch a
tmp directory without dancing around the singleton.

Wave D introduced ``<name>.kohakutr.v2`` as the live file alongside
the bare ``<name>.kohakutr`` v1 rollback companion. These helpers
hide that detail from the routes — every callsite asks for "the file
for session ``foo``" and gets the highest-versioned one.
"""

from pathlib import Path


def normalize_session_stem(path: Path) -> str:
    """Return the stable session name regardless of which version
    suffix the file carries.

    ``foo.kohakutr.v2`` → ``foo`` (Wave D-migrated v2 file).
    ``foo.kohakutr``     → ``foo`` (v1 / unversioned).
    ``foo.kt``           → ``foo`` (legacy short form).
    """
    name = path.name
    if name.endswith(".kohakutr"):
        return name[: -len(".kohakutr")]
    if name.endswith(".kt"):
        return name[: -len(".kt")]
    if ".kohakutr.v" in name:
        idx = name.find(".kohakutr.v")
        return name[:idx]
    return path.stem


def all_session_files(session_dir: Path) -> list[Path]:
    """Every ``.kohakutr`` / ``.kohakutr.v*`` / ``.kt`` file on disk."""
    if not session_dir.exists():
        return []
    return (
        list(session_dir.glob("*.kohakutr"))
        + list(session_dir.glob("*.kohakutr.v*"))
        + list(session_dir.glob("*.kt"))
    )


def _version_rank(path: Path) -> int:
    """Numeric version of a versioned session file (``foo.kohakutr.v2`` → 2)."""
    name = path.name
    if ".kohakutr.v" in name:
        tail = name.rsplit(".v", 1)[-1]
        return int(tail) if tail.isdigit() else 0
    return 0


def resolve_session_path(session_name: str, session_dir: Path) -> Path | None:
    """Shared session file lookup (name, prefix, or full path).

    Honors Wave D auto-migration: when the same logical session has
    both ``foo.kohakutr`` and ``foo.kohakutr.v2`` on disk, prefer the
    highest version (``.v2`` > bare ``.kohakutr``). Bare ``.kohakutr``
    is preserved as the rollback file.
    """
    if not session_dir.exists():
        return None

    versions = sorted(
        (
            (int(p.name.rsplit(".v", 1)[1]), p)
            for p in session_dir.glob(f"{session_name}.kohakutr.v*")
            if p.name.rsplit(".v", 1)[-1].isdigit()
        ),
        reverse=True,
    )
    if versions:
        return versions[0][1]

    for ext in (".kohakutr", ".kt"):
        candidate = session_dir / f"{session_name}{ext}"
        if candidate.exists():
            return candidate

    matches = [
        p
        for p in all_session_files(session_dir)
        if normalize_session_stem(p) == session_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return max(matches, key=_version_rank)

    fuzzy = [
        p
        for p in all_session_files(session_dir)
        if normalize_session_stem(p).startswith(session_name)
        or session_name in normalize_session_stem(p)
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def all_versions_for_session(session_name: str, session_dir: Path) -> list[Path]:
    """Every on-disk file that belongs to the given session name.

    Used by delete to clean up both the live ``.v2`` AND its v1
    rollback companion in one shot.
    """
    return [
        p
        for p in all_session_files(session_dir)
        if normalize_session_stem(p) == session_name
    ]


def pick_canonical_per_session(session_dir: Path) -> list[Path]:
    """Return one path per logical session — the highest-versioned file
    when both v1 + v2 are present.

    Used by the listing endpoint so the user sees one row per session
    even when Wave D has left a v1 rollback alongside the v2 file.
    """
    by_canonical: dict[str, Path] = {}
    for path in all_session_files(session_dir):
        key = normalize_session_stem(path)
        existing = by_canonical.get(key)
        if existing is None or _version_rank(path) > _version_rank(existing):
            by_canonical[key] = path
    return list(by_canonical.values())
