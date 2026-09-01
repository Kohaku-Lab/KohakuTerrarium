"""KTLogger: a ``logging.Logger`` whose level methods take structured fields.

Split out of :mod:`kohakuterrarium.utils.logging` so the explicit level-method
overrides stay under the file-size cap. The overrides exist so type checkers
accept the project's ``logger.info("msg", field=value)`` convention; the merge
itself happens once, in :meth:`KTLogger._log`.

Each override adds one frame between the caller and ``Logger._log``.
``findCaller`` only skips frames belonging to the stdlib logging module, so both
the level methods and ``_log`` compensate with ``stacklevel + 1`` to keep
``filename`` / ``lineno`` / ``funcName`` pointing at the real call site.
"""

import logging
from collections.abc import Mapping
from typing import Any


class KTLogger(logging.Logger):
    """Extended logger accepting extra record fields as keyword arguments."""

    def _log(
        self,
        level: int,
        msg: object,
        args: tuple[object, ...] | Mapping[str, object],
        exc_info: Any = None,
        extra: Mapping[str, Any] | None = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        **kwargs: Any,
    ) -> None:
        # Build a fresh mapping: callers may reuse the dict they passed as extra.
        merged: dict[str, Any] | None = None
        if extra is not None or kwargs:
            merged = dict(extra) if extra else {}
            merged.update(kwargs)
        super()._log(level, msg, args, exc_info, merged, stack_info, stacklevel + 1)

    def debug(
        self,
        msg: object,
        *args: object,
        exc_info: Any = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if self.isEnabledFor(logging.DEBUG):
            self._log(
                logging.DEBUG,
                msg,
                args,
                exc_info=exc_info,
                extra=extra,
                stack_info=stack_info,
                stacklevel=stacklevel + 1,
                **kwargs,
            )

    def info(
        self,
        msg: object,
        *args: object,
        exc_info: Any = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if self.isEnabledFor(logging.INFO):
            self._log(
                logging.INFO,
                msg,
                args,
                exc_info=exc_info,
                extra=extra,
                stack_info=stack_info,
                stacklevel=stacklevel + 1,
                **kwargs,
            )

    def warning(
        self,
        msg: object,
        *args: object,
        exc_info: Any = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if self.isEnabledFor(logging.WARNING):
            self._log(
                logging.WARNING,
                msg,
                args,
                exc_info=exc_info,
                extra=extra,
                stack_info=stack_info,
                stacklevel=stacklevel + 1,
                **kwargs,
            )

    def error(
        self,
        msg: object,
        *args: object,
        exc_info: Any = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if self.isEnabledFor(logging.ERROR):
            self._log(
                logging.ERROR,
                msg,
                args,
                exc_info=exc_info,
                extra=extra,
                stack_info=stack_info,
                stacklevel=stacklevel + 1,
                **kwargs,
            )

    def critical(
        self,
        msg: object,
        *args: object,
        exc_info: Any = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if self.isEnabledFor(logging.CRITICAL):
            self._log(
                logging.CRITICAL,
                msg,
                args,
                exc_info=exc_info,
                extra=extra,
                stack_info=stack_info,
                stacklevel=stacklevel + 1,
                **kwargs,
            )

    def exception(
        self,
        msg: object,
        *args: object,
        exc_info: Any = True,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if self.isEnabledFor(logging.ERROR):
            self._log(
                logging.ERROR,
                msg,
                args,
                exc_info=exc_info,
                extra=extra,
                stack_info=stack_info,
                stacklevel=stacklevel + 1,
                **kwargs,
            )

    def log(
        self,
        level: int,
        msg: object,
        *args: object,
        exc_info: Any = None,
        stack_info: bool = False,
        stacklevel: int = 1,
        extra: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(level, int):
            if logging.raiseExceptions:
                raise TypeError("level must be an integer")
            return
        if self.isEnabledFor(level):
            self._log(
                level,
                msg,
                args,
                exc_info=exc_info,
                extra=extra,
                stack_info=stack_info,
                stacklevel=stacklevel + 1,
                **kwargs,
            )
