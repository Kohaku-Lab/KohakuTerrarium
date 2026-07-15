"""Resolve shared and per-user filesystem namespaces.

User directories are keyed by immutable numeric IDs so username changes do not move
state. Enabling user isolation does not implicitly reassign existing shared sessions
or preferences; operators must choose the user that receives migrated state.
"""

from pathlib import Path

from kohakuterrarium.utils.config_dir import config_dir


def user_config_dir(user_id: int) -> Path:
    """Return the user's config root, creating it on first access."""
    path = config_dir() / "users" / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_session_dir(user_id: int) -> Path:
    """Return the user's session directory, creating it on first access."""
    path = user_config_dir(user_id) / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_ui_prefs_path(user_id: int) -> Path:
    """Return the user's UI preferences path with an existing parent directory."""
    return user_config_dir(user_id) / "ui_prefs.json"


def shared_session_dir() -> Path:
    """Return the shared session directory used without user isolation or for migration."""
    return config_dir() / "sessions"


def shared_ui_prefs_path() -> Path:
    """Return the shared UI preferences path used without user isolation or for migration."""
    return config_dir() / "ui_prefs.json"


__all__ = [
    "shared_session_dir",
    "shared_ui_prefs_path",
    "user_config_dir",
    "user_session_dir",
    "user_ui_prefs_path",
]
