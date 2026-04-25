"""Session format migration registry.

Wave D — non-destructive, chained, lazy migration from older
``.kohakutr`` formats to the current :data:`FORMAT_VERSION`.

File naming
-----------

* ``alice.kohakutr`` — bare path. Treated as v1 implicit unless its
  ``meta["format_version"]`` says otherwise. Never rewritten by a
  migrator; the original stays on disk so users can downgrade.
* ``alice.kohakutr.v2`` — explicit v2, produced by migration (or by a
  future user importing a v2 file).
* ``alice.kohakutr.v<N>`` — same convention for later formats.

Usage
-----

``ensure_latest_version(path)`` discovers every version file sharing a
basename, picks the newest readable one, and (if necessary) migrates
it up to :data:`MAX_SUPPORTED_VERSION`. Returns the path the caller
should open. The original v1 file is *never* modified.

Registering a new migrator only requires adding one entry to
:data:`MIGRATORS` and bumping :data:`MAX_SUPPORTED_VERSION`.
"""

import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from kohakuterrarium.session.migrations import v1_to_v2
from kohakuterrarium.session.version import FORMAT_VERSION, detect_format_version
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Highest format version this framework can produce. Bumping this
# requires registering the ``(MAX - 1, MAX)`` migrator below.
MAX_SUPPORTED_VERSION: int = FORMAT_VERSION

# Registry of version migrators. Each value is a callable
# ``(src_path: str, dst_path: str) -> None`` that reads ``src`` and
# writes a freshly-built store at ``dst``. Migrators MUST NOT modify
# the source file.
MIGRATORS: dict[tuple[int, int], Callable[[str, str], None]] = {
    (1, 2): v1_to_v2.migrate,
}

# Matches the ``.vN`` suffix at the end of a kohakutr path.
_VERSION_SUFFIX_RE = re.compile(r"\.v(\d+)$")


def _strip_version_suffix(path: Path) -> Path:
    """Return the bare ``.kohakutr`` path for a versioned file.

    ``alice.kohakutr.v2`` → ``alice.kohakutr``. Paths that already
    lack a version suffix are returned unchanged.
    """
    match = _VERSION_SUFFIX_RE.search(path.name)
    if match is None:
        return path
    return path.with_name(path.name[: match.start()])


def _version_from_suffix(path: Path) -> int | None:
    """Parse an integer version from a ``.vN`` suffix, or return ``None``."""
    match = _VERSION_SUFFIX_RE.search(path.name)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def path_for_version(base_path: str | Path, version: int) -> Path:
    """Return the on-disk path for a specific format version.

    v1 uses the bare path (no suffix). v2+ uses the ``.v<N>`` suffix
    so the original file is never overwritten.
    """
    bare = _strip_version_suffix(Path(base_path))
    if version <= 1:
        return bare
    return bare.with_name(f"{bare.name}.v{version}")


def discover_versions(base_path: str | Path) -> list[tuple[int, Path]]:
    """Return every version file sharing ``base_path``'s basename.

    Output is sorted descending by version. The bare ``.kohakutr``
    file's version is read from its ``meta["format_version"]`` (so a
    freshly-written v2 bare file reports as v2 here). Files with a
    ``.v<N>`` suffix are trusted to encode their version in the name.

    Missing files are skipped silently; this is a read probe, not a
    guarantee that any file exists.
    """
    bare = _strip_version_suffix(Path(base_path))
    parent = bare.parent if bare.parent != Path("") else Path(".")
    pattern = f"{bare.name}*"
    result: dict[int, Path] = {}

    for candidate in parent.glob(pattern):
        if candidate == bare and candidate.exists():
            try:
                version = detect_format_version(candidate)
            except Exception as e:
                logger.debug(
                    "Failed to probe bare session version",
                    path=str(candidate),
                    error=str(e),
                )
                version = 1
            # Prefer the bare path entry only when no suffix file has
            # claimed this version already (avoids shadowing an
            # explicit ``.v2`` that matches meta of the bare file).
            result.setdefault(version, candidate)
            continue

        suffix_version = _version_from_suffix(candidate)
        if suffix_version is None:
            continue
        # ``.v1`` files should not exist (v1 uses the bare path), but
        # if someone creates one, treat them the same as the bare v1.
        result[suffix_version] = candidate

    return sorted(result.items(), key=lambda pair: pair[0], reverse=True)


def _chain(src_version: int, dst_version: int) -> list[tuple[int, int]]:
    """Build a chain of registered migrators from ``src`` to ``dst``.

    Walks the registry picking the smallest next-step each time. Raises
    ``ValueError`` if the chain cannot be completed.
    """
    chain: list[tuple[int, int]] = []
    current = src_version
    while current < dst_version:
        next_steps = sorted(
            (pair for pair in MIGRATORS if pair[0] == current),
            key=lambda pair: pair[1],
        )
        if not next_steps:
            raise ValueError(
                f"No migrator registered from format v{current} (target v{dst_version})"
            )
        step = next_steps[0]
        chain.append(step)
        current = step[1]
    return chain


def migrate(source_path: str | Path, target_version: int) -> Path:
    """Migrate ``source_path`` upward until its version ≥ ``target_version``.

    Returns the path of the final migrated file. The source file is
    preserved on disk; each intermediate step writes a new file at
    ``path_for_version(source_path, step_dst)``.

    If the final destination file already exists, it is reused (the
    migration is treated as idempotent — the prior migration already
    produced it).

    On failure, any partial destination file created during this call
    is removed and the exception is re-raised with the source path in
    the message so the caller can recover.
    """
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(src)

    current_version = detect_format_version(src)
    if current_version >= target_version:
        return src

    chain = _chain(current_version, target_version)
    current_path = src
    for src_v, dst_v in chain:
        migrator = MIGRATORS[(src_v, dst_v)]
        dst_path = path_for_version(src, dst_v)
        if dst_path.exists():
            logger.info(
                "Session migration target already present",
                source=str(current_path),
                destination=str(dst_path),
                src_version=src_v,
                dst_version=dst_v,
            )
            current_path = dst_path
            continue
        logger.info(
            "Migrating session format",
            source=str(current_path),
            destination=str(dst_path),
            src_version=src_v,
            dst_version=dst_v,
        )
        try:
            migrator(str(current_path), str(dst_path))
        except Exception as exc:
            # Clean up only the partial file from *this* step — leave
            # any earlier intermediate files alone; they are valid on
            # their own and the user may want them for diagnostics.
            if dst_path.exists():
                try:
                    dst_path.unlink()
                except OSError as cleanup_exc:
                    logger.warning(
                        "Failed to remove partial migration output",
                        path=str(dst_path),
                        error=str(cleanup_exc),
                    )
            raise RuntimeError(
                f"Session migration v{src_v}→v{dst_v} failed for {src}: {exc}"
            ) from exc
        current_path = dst_path

    return current_path


def ensure_latest_version(base_path: str | Path) -> Path:
    """Pick the newest readable version for ``base_path``, migrating if needed.

    Walks :func:`discover_versions`, chooses the highest-version file
    that the current framework can read (``version ≤
    MAX_SUPPORTED_VERSION``), and migrates it up if it is below
    :data:`MAX_SUPPORTED_VERSION`. Returns the path the caller should
    open.
    """
    candidates = discover_versions(base_path)
    if not candidates:
        # Caller asked for a file that doesn't exist. Return the input
        # path so the caller gets a normal FileNotFound at open time.
        return Path(base_path)

    readable = [c for c in candidates if c[0] <= MAX_SUPPORTED_VERSION]
    if not readable:
        # Every on-disk version is newer than us. Fall back to the
        # user-provided path (probably an error downstream).
        logger.warning(
            "All session files exceed supported format",
            base_path=str(base_path),
            max_supported=MAX_SUPPORTED_VERSION,
        )
        return candidates[0][1]

    best_version, best_path = readable[0]
    if best_version >= MAX_SUPPORTED_VERSION:
        return best_path

    logger.info(
        "Auto-migrating session to newest format",
        source=str(best_path),
        source_version=best_version,
        target_version=MAX_SUPPORTED_VERSION,
    )
    return migrate(best_path, MAX_SUPPORTED_VERSION)


def migration_marker() -> str:
    """Return an ISO-8601 UTC timestamp used in migration metadata."""
    return datetime.now(timezone.utc).isoformat()
