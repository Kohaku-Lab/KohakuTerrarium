"""Session format version helpers.

Wave D introduces the migration framework. ``FORMAT_VERSION`` is the
version new stores write into ``meta["format_version"]``. Bumping it
requires a matching migrator in
:mod:`kohakuterrarium.session.migrations`.

File naming: the bare ``<name>.kohakutr`` slot is reserved for the
original v1 file and is never rewritten by the migrator. Upgraded
files use the ``<name>.kohakutr.v<N>`` suffix so old frameworks can
still read the v1 file alongside the new one.
"""

from pathlib import Path

from kohakuvault import KVault

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# The format version new stores initialise in ``meta``. Incrementing
# this constant requires registering a ``(src, dst)`` migrator in
# :mod:`kohakuterrarium.session.migrations`.
FORMAT_VERSION: int = 2


def detect_format_version(path: str | Path) -> int:
    """Return the ``meta["format_version"]`` stored in a ``.kohakutr`` file.

    Opens only the ``meta`` KVault so we don't restore event counters or
    touch FTS. Defaults to ``1`` when the key is absent — v1 was the
    implicit format before this field existed.
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
                logger.debug(
                    "Failed to close meta while probing format_version",
                    error=str(e),
                )
    except Exception as e:
        logger.debug(
            "detect_format_version fell back to 1",
            path=str(p),
            error=str(e),
            exc_info=True,
        )
        return 1
