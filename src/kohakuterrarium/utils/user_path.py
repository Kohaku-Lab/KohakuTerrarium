"""Add and remove a directory on the user's own PATH, without administrator rights.

Windows edits the per-user ``Path`` value in the registry and tells running
programs the environment changed. POSIX shells read a marked block that this
module owns in the login profile. Every step is reversible and touches nothing
outside the marked entry.
"""

import ctypes
import os
import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

BLOCK_START = "# >>> kohakuterrarium >>>"
BLOCK_END = "# <<< kohakuterrarium <<<"
_BROADCAST = 0xFFFF
_SETTING_CHANGE = 0x001A


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b)
    )


def split_entries(value: str) -> list[str]:
    return [entry for entry in value.split(os.pathsep) if entry]


def with_entry(value: str, directory: str) -> str:
    """Return ``value`` with ``directory`` present exactly once."""
    entries = split_entries(value)
    if any(_same(entry, directory) for entry in entries):
        return value
    return os.pathsep.join([*entries, directory])


def without_entry(value: str, directory: str) -> str:
    return os.pathsep.join(
        entry for entry in split_entries(value) if not _same(entry, directory)
    )


def contains(value: str, directory: str) -> bool:
    return any(_same(entry, directory) for entry in split_entries(value))


def profile_block(directory: str) -> str:
    return f'{BLOCK_START}\nexport PATH="{directory}:$PATH"\n{BLOCK_END}\n'


def profile_with_block(text: str, directory: str) -> str:
    """Return profile text holding exactly one block for ``directory``."""
    kept = profile_without_block(text).rstrip("\n")
    return kept + ("\n\n" if kept else "") + profile_block(directory)


def profile_without_block(text: str) -> str:
    start = text.find(BLOCK_START)
    if start == -1:
        return text
    end = text.find(BLOCK_END, start)
    if end == -1:
        return text
    remainder = text[end + len(BLOCK_END) :].lstrip("\n")
    head = text[:start].rstrip("\n")
    return head + ("\n\n" if head and remainder else "") + remainder


def profile_path() -> Path:
    name = ".zprofile" if sys.platform == "darwin" else ".profile"
    return Path.home() / name


def _read_registry_path() -> str:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            return winreg.QueryValueEx(key, "Path")[0]
        except FileNotFoundError:
            return ""


def _write_registry_path(value: str) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)
    ctypes.windll.user32.SendMessageTimeoutW(
        _BROADCAST, _SETTING_CHANGE, 0, "Environment", 2, 5000, None
    )


def is_on_user_path(directory: Path) -> bool:
    """Whether new terminals will find ``directory``."""
    if sys.platform == "win32":
        return contains(_read_registry_path(), str(directory))
    try:
        text = profile_path().read_text(encoding="utf-8")
    except OSError:
        return False
    return BLOCK_START in text and str(directory) in text


def add_to_user_path(directory: Path) -> None:
    if sys.platform == "win32":
        _write_registry_path(with_entry(_read_registry_path(), str(directory)))
        return
    path = profile_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    path.write_text(profile_with_block(text, str(directory)), encoding="utf-8")


def remove_from_user_path(directory: Path) -> None:
    if sys.platform == "win32":
        _write_registry_path(without_entry(_read_registry_path(), str(directory)))
        return
    path = profile_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    path.write_text(profile_without_block(text), encoding="utf-8")
