"""Path-safety helpers for studio FS operations (legacy shim).

The canonical home is :mod:`kohakuterrarium.studio.editors.utils_paths`.
This re-export keeps legacy imports working until callers migrate.
"""

from kohakuterrarium.studio.editors.utils_paths import (
    UnsafePath,
    ensure_in_root,
    sanitize_name,
)

__all__ = ["UnsafePath", "ensure_in_root", "sanitize_name"]
