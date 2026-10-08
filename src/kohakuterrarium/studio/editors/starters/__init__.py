"""Starting points for new creatures and modules (see :mod:`.catalog`)."""

from kohakuterrarium.studio.editors.starters.catalog import (
    DEFAULT_STARTER,
    STARTER_KINDS,
    Starter,
    UnknownStarterError,
    get_starter,
    list_starters,
    starter_form,
)

__all__ = [
    "DEFAULT_STARTER",
    "STARTER_KINDS",
    "Starter",
    "UnknownStarterError",
    "get_starter",
    "list_starters",
    "starter_form",
]
