"""Round-trip YAML IO for creature configs.

Uses ruamel.yaml in round-trip mode so comments + key order on
existing files survive re-saves. ``save_creature_merged`` deep-
merges an incoming patch into the existing document, replacing
scalars and lists wholesale but recursing into mappings so
preserved comments stay anchored to their keys.
"""

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _yaml() -> YAML:
    """Fresh YAML instance configured for creature configs."""
    y = YAML(typ="rt")
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 120
    y.preserve_quotes = True
    y.explicit_start = False
    return y


def load_creature_file(path: Path) -> dict:
    """Load a YAML creature config as a ruamel CommentedMap.

    Returns ``{}`` on empty file. Raises ``FileNotFoundError`` if
    the path doesn't exist — callers decide how to react.
    """
    y = _yaml()
    with path.open("r", encoding="utf-8") as f:
        data = y.load(f)
    return data if data is not None else {}


def save_creature_file(path: Path, data: dict[str, Any]) -> None:
    """Overwrite the file with *data*.

    Loses comments the incoming dict doesn't carry. Prefer
    ``save_creature_merged`` when a file already exists.
    """
    y = _yaml()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        y.dump(data, f)
    tmp.replace(path)


def save_creature_merged(path: Path, incoming: dict) -> None:
    """Merge *incoming* into the document at *path*, preserving comments.

    If the file doesn't exist, writes *incoming* verbatim (no
    comments to preserve). Deep-merges mappings, replaces lists
    and scalars wholesale.
    """
    y = _yaml()
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            doc = y.load(f)
        if doc is None:
            doc = CommentedMap()
    else:
        doc = CommentedMap()
    _deep_merge(doc, incoming)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        y.dump(doc, f)
    tmp.replace(path)


def _deep_merge(target: Any, incoming: Any) -> None:
    """Recursively merge *incoming* into *target* in place."""
    if isinstance(target, CommentedMap) and isinstance(incoming, dict):
        # Remove keys present in target but not in incoming? No —
        # incoming is a patch, not a replacement. Callers that
        # want full replacement call save_creature_file instead.
        for k, v in incoming.items():
            if (
                k in target
                and isinstance(target[k], (CommentedMap, dict))
                and isinstance(v, dict)
            ):
                _deep_merge(target[k], v)
            else:
                target[k] = v
    elif isinstance(target, list) and isinstance(incoming, list):
        # Lists are replaced wholesale — merging by index or by a
        # "name" field is ambiguous and differs per section.
        # Callers send the full desired list.
        target[:] = incoming
