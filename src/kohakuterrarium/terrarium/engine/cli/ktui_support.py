"""Whether the installed kohakutui can run ktui, checked before anything imports ktui.

kohakutui is not a declared dependency until the release ktui needs is published (0.2.0);
until then ``--mode ktui`` runs only against a source install of KohakuTUI. The check looks
for the features ktui uses rather than a version, so a source checkout that still reports the
last released version passes, and kohakutui 0.1.0 from PyPI does not.
"""

import importlib

# what ktui needs beyond kohakutui 0.1.0: form controls, layout and bar widgets
REQUIRED = ("Field", "Columns", "Tabs", "Bars")
INSTALL = (
    "ktui needs kohakutui 0.2, which is not released yet: install KohakuTUI from source "
    "(pip install -e <path to KohakuTUI>)."
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


__all__ = ["ktui_unavailable"]
