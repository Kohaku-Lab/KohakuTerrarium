"""CLI generic config — show/path/edit for KohakuTerrarium config files."""

from kohakuterrarium.studio.identity.settings import (
    edit_config,
    show_path,
    show_paths,
)


def show_cli() -> int:
    """Print all configuration file paths."""
    return show_paths()


def path_cli(name: str | None) -> int:
    """Print one named configuration path."""
    return show_path(name)


def edit_cli(name: str | None) -> int:
    """Open one named configuration file in the editor."""
    return edit_config(name)
