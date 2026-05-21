"""Per-user filesystem namespace helpers.

Path layout when L4 is enabled:

::

    <config_dir>/
    ├── auth.db
    ├── api_keys.yaml          # SHARED — admin-managed
    ├── llm_profiles.yaml      # SHARED — admin-managed
    ├── mcp_servers.yaml       # SHARED — admin-managed
    ├── ui_prefs.json          # present in L4=off mode; per-user when L4=on
    └── users/
        ├── 1/                 # user id (rename-safe)
        │   ├── ui_prefs.json
        │   ├── tabs.json
        │   └── sessions/
        │       └── <session>.kohakutr
        ├── 2/
        │   └── ...
        └── ...

When the operator first enables L4 on a running host, existing
top-level state is NOT auto-moved — they explicitly run
``kt admin migrate --from-shared-state --to-user <username>`` to
claim it.  This is the deliberate design from §9.5 of the design
doc: avoid surprises in multi-user upgrades.
"""

from pathlib import Path

from kohakuterrarium.utils.config_dir import config_dir


def user_config_dir(user_id: int) -> Path:
    """Per-user config root.  Created on first access."""
    path = config_dir() / "users" / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_session_dir(user_id: int) -> Path:
    """Per-user ``.kohakutr`` session directory.  Created on first access."""
    path = user_config_dir(user_id) / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_ui_prefs_path(user_id: int) -> Path:
    """Per-user UI prefs JSON path.  Parent dir guaranteed to exist."""
    return user_config_dir(user_id) / "ui_prefs.json"


def shared_session_dir() -> Path:
    """The L4-off / migration-source shared session dir."""
    return config_dir() / "sessions"


def shared_ui_prefs_path() -> Path:
    """The L4-off / migration-source shared ui_prefs.json."""
    return config_dir() / "ui_prefs.json"


__all__ = [
    "shared_session_dir",
    "shared_ui_prefs_path",
    "user_config_dir",
    "user_session_dir",
    "user_ui_prefs_path",
]
