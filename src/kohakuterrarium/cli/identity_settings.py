"""CLI generic config — show/path/edit for KohakuTerrarium config files."""

from kohakuterrarium.studio.identity.settings import (
    edit_config,
    show_path,
    show_paths,
)


def show_cli() -> int:
    return show_paths()


def path_cli(name: str | None) -> int:
    return show_path(name)


def edit_cli(name: str | None) -> int:
    return edit_config(name)
