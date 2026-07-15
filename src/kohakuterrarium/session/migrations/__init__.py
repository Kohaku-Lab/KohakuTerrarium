"""Discover and non-destructively migrate versioned session files.

Bare files retain their detected version, while migrated versions use ``.vN``
suffixes so source files remain available.
"""

import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from kohakuterrarium.session.migrations import v1_to_v2
from kohakuterrarium.session.version import FORMAT_VERSION, detect_format_version
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Every supported version increment requires a registered migration path.
MAX_SUPPORTED_VERSION: int = FORMAT_VERSION

# Migrators build a fresh destination and must never modify their source.
MIGRATORS: dict[tuple[int, int], Callable[[str, str], None]] = {
    (1, 2): v1_to_v2.migrate,
}

_VERSION_SUFFIX_RE = re.compile(r"\.v(\d+)$")


def _strip_version_suffix(path: Path) -> Path:
    """Return the bare ``.kohakutr`` path for a versioned file.

    Paths without a version suffix are returned unchanged.
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

    Version 1 uses the bare path; later versions use ``.vN`` suffixes.
    """
    bare = _strip_version_suffix(Path(base_path))
    if version <= 1:
        return bare
    return bare.with_name(f"{bare.name}.v{version}")


def discover_versions(base_path: str | Path) -> list[tuple[int, Path]]:
    """Return every version file sharing ``base_path``'s basename.

    Results are descending by version. Bare-file versions come from metadata;
    suffixed files trust their filename. Missing candidates are ignored.
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
                logger.warning(
                    "Failed to probe bare session version",
                    path=str(candidate),
                    error=str(e),
                    exc_info=True,
                )
                version = 1
            # An explicit suffixed file takes precedence over an equivalent bare file.
            result.setdefault(version, candidate)
            continue

        suffix_version = _version_from_suffix(candidate)
        if suffix_version is None:
            continue
        # Tolerate explicit v1 suffixes even though version 1 is normally bare.
        result[suffix_version] = candidate

    return sorted(result.items(), key=lambda pair: pair[0], reverse=True)


def _chain(src_version: int, dst_version: int) -> list[tuple[int, int]]:
    """Build a chain of registered migrators from ``src`` to ``dst``.

    Choose the smallest registered upward step at each version and raise when
    the target is unreachable.
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

    Each step writes a separate versioned file and reuses an existing destination.
    Failed steps remove only their partial output and preserve valid earlier files.
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
            # Earlier migration products remain valid independent session files.
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

    Select the newest readable candidate and migrate it to the framework's
    maximum supported version when necessary.
    """
    candidates = discover_versions(base_path)
    if not candidates:
        # Preserve normal file-not-found behavior at the eventual open call.
        return Path(base_path)

    readable = [c for c in candidates if c[0] <= MAX_SUPPORTED_VERSION]
    if not readable:
        # Return the newest candidate so the downstream reader reports incompatibility.
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
