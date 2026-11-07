"""Whether the installed kohakutui can run ktui, checked before anything imports ktui.

kohakutui comes with the ``ktui`` extra (``kohakutui>=0.2.0``), the release trees and the
one-line installers; plain ``pip install kohakuterrarium`` leaves it out, so KT runs without
it. The check looks for the features ktui uses rather than a version, so a source checkout that
still reports an older version passes, and kohakutui 0.1.0 from PyPI does not.
"""

import importlib

# what ktui needs beyond kohakutui 0.1.0: form controls, layout, bar, diff and image widgets
REQUIRED = ("Field", "Columns", "Tabs", "Bars", "Diff", "Image")
INSTALL = (
    'ktui needs kohakutui 0.2 or newer: pip install "kohakuterrarium[ktui]" (or KohakuTUI '
    "from source: pip install -e <path to KohakuTUI>)."
)


def ktui_unavailable() -> str | None:
    """Why ``--mode ktui`` cannot run here, or None when it can."""
    try:
        module = importlib.import_module("kohakutui")
    except ImportError:
        return f"--mode ktui: kohakutui is not installed. {INSTALL}"
    missing = [name for name in REQUIRED if not hasattr(module, name)]
    text = getattr(module, "Text", None)
    if "link" not in getattr(text, "__dataclass_fields__", {}):
        missing.append("Text.link")
    if missing:
        version = getattr(module, "__version__", "?")
        return (
            f"--mode ktui: the installed kohakutui {version} is too old "
            f"(no {', '.join(missing)}). {INSTALL}"
        )
    return None


def resume_command(io_mode: str | None) -> str:
    """The command that resumes a session in the surface it ran in (ktui: ``kt-ui``)."""
    return "kt-ui resume" if io_mode == "ktui" else "kt resume"


# ``kt-ui --inline|--hybrid|--fullscreen``: the renderer for this run, over the saved one
RENDERER_ENV = "KT_KTUI_RENDERER"

# a session ktui's /resume chose: the launcher that started ktui resumes it once ktui left
_next_session: str | None = None


def request_session(path: str) -> None:
    """Asks the launcher to resume ``path`` after the current session ends."""
    global _next_session
    _next_session = path


def take_next_session() -> str | None:
    """The session ktui asked to resume next (once), or None."""
    global _next_session
    path, _next_session = _next_session, None
    return path


__all__ = [
    "RENDERER_ENV",
    "resume_command",
    "ktui_unavailable",
    "request_session",
    "take_next_session",
]
