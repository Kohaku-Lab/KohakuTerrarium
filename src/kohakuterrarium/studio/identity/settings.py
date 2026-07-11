"""Generic config settings — show/path/edit for the global config files."""

import os
from pathlib import Path

from kohakuterrarium.llm.api_keys import KEYS_PATH
from kohakuterrarium.llm.profiles import PROFILES_PATH
from kohakuterrarium.studio.identity.mcp_servers import MCP_SERVERS_PATH
from kohakuterrarium.studio.identity.ui_prefs import UI_PREFS_PATH


def config_paths() -> dict[str, Path]:
    """Return the canonical mapping of config-key to path."""
    return {
        "home": Path.home() / ".kohakuterrarium",
        "llm_profiles": PROFILES_PATH,
        "api_keys": KEYS_PATH,
        "mcp_servers": MCP_SERVERS_PATH,
        "ui_prefs": UI_PREFS_PATH,
    }


def show_paths() -> int:
    """Print the configured paths. Returns CLI exit code."""
    paths = config_paths()
    print("KohakuTerrarium config paths")
    for name, path in paths.items():
        print(f"  {name:<12} {path}")
    return 0


def show_path(name: str | None) -> int:
    """Print a single path or fall back to ``show_paths()``."""
    paths = config_paths()
    if not name:
        return show_paths()
    path = paths.get(name)
    if not path:
        print(f"Unknown config path key: {name}")
        print(f"Available: {', '.join(paths.keys())}")
        return 1
    print(path)
    return 0


def edit_config(name: str | None) -> int:
    """Open a config file in ``$EDITOR``. ``name`` defaults to ``llm_profiles``."""
    paths = config_paths()
    key = name or "llm_profiles"
    path = paths.get(key)
    if not path:
        print(f"Unknown config target: {key}")
        print(f"Available: {', '.join(paths.keys())}")
        return 1
    editor = os.environ.get("EDITOR")
    if not editor:
        print("$EDITOR is not set.")
        print(path)
        return 1
    # Validate editor: must be a single executable name (no shell metacharacters)
    # to prevent command injection via crafted $EDITOR values.
    if not editor.isidentifier() and "/" not in editor and "\\" not in editor:
        # Allow simple names (vim, nano) and paths (/usr/bin/vim) but reject
        # shell metacharacters that could break out of the intended command.
        safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-_.")
        if not all(c in safe_chars for c in editor):
            print(f"$EDITOR contains unsafe characters: {editor!r}")
            print(path)
            return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    # Use subprocess.run with a list to avoid shell interpretation of $EDITOR.
    import subprocess
    return subprocess.run([editor, str(path)]).returncode
