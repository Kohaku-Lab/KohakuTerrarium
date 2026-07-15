"""Define and detect the persisted session format version.

Version increments require a registered migration path, and migrated files use
versioned suffixes so original sources remain intact.
"""

from pathlib import Path

from kohakuvault import KVault

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Increment only with a registered migration path to the new version.
FORMAT_VERSION: int = 2


def detect_format_version(path: str | Path) -> int:
    """Return the ``meta["format_version"]`` stored in a ``.kohakutr`` file.

    Only metadata is opened. Missing or invalid version values resolve to version
    1, whose files predate the explicit field.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    try:
        meta = KVault(str(p), table="meta")
        try:
            meta.enable_auto_pack()
            try:
                val = meta["format_version"]
            except KeyError:
                return 1
            if isinstance(val, int):
                return val
            try:
                return int(val)
            except (TypeError, ValueError):
                return 1
        finally:
            try:
                meta.close()
            except Exception as e:
                logger.warning(
                    "Failed to close meta while probing format_version",
                    error=str(e),
                    exc_info=True,
                )
    except Exception as e:
        logger.warning(
            "detect_format_version fell back to 1",
            path=str(p),
            error=str(e),
            exc_info=True,
        )
        return 1
