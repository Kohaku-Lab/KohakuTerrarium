"""Provide launcher-local logging before the framework is importable.

The standard-library logger writes a rotating configuration-local file and
stderr. Keeping this module independent of framework logging allows clean
first-launch and recovery paths to report failures.
"""

import logging
from logging.handlers import RotatingFileHandler

from kohakuterrarium.launcher.paths import config_home

_LOGGER_NAME = "kt-launcher"
_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Return the singleton launcher logger with handlers installed once."""
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
        # File logging is optional when the configuration directory is read-only.
        pass

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    _logger = logger
    return logger


__all__ = ["get_logger"]
