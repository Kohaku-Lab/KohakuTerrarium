"""Minimal launcher-local logger.

Cannot reuse :mod:`kohakuterrarium.utils.logging` because the wrapper
runs BEFORE the framework's site-packages are guaranteed to be on
``sys.path`` (clean first launch, recovery mode).  A pure-stdlib
logger keeps the dependency surface to zero framework imports.

Writes to ``~/.kohakuterrarium/logs/launcher.log`` (rotating, 1MB×3)
plus stderr.  Single-line format so the file is grep-friendly.
"""

import logging
from logging.handlers import RotatingFileHandler

from kohakuterrarium.launcher.paths import config_home

_LOGGER_NAME = "kt-launcher"
_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Return the singleton launcher logger.

    Idempotent: first call wires the handlers; subsequent calls return
    the same instance without re-installing handlers (otherwise repeated
    invocations of :func:`bootloader.main` would duplicate lines).
    """
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(_FORMAT)

    log_dir = config_home() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "launcher.log",
            maxBytes=1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # Read-only filesystem (some sandboxes) — log only to stderr.
        pass

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    _logger = logger
    return logger


__all__ = ["get_logger"]
