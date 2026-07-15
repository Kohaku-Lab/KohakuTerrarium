"""Round-trip YAML IO for creature configs.

Uses ruamel.yaml round-trip mode to preserve comments, quoting, and key order.
Merged saves recurse into mappings while replacing scalar and list values, which
keeps existing comments anchored to their keys.
"""

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _yaml() -> YAML:
    """Return a fresh round-trip YAML serializer for creature configs."""
    y = YAML(typ="rt")
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 120
    y.preserve_quotes = True
    y.explicit_start = False
    return y


def load_creature_file(path: Path) -> dict:
    """Load a creature config while preserving round-trip metadata.

    Empty files return ``{}``; missing files propagate ``FileNotFoundError``.
    """
    y = _yaml()
    with path.open("r", encoding="utf-8") as f:
        data = y.load(f)
    return data if data is not None else {}


def save_creature_file(path: Path, data: dict[str, Any]) -> None:
    """Atomically overwrite a creature config with the supplied mapping.

    Comments absent from ``data`` are not preserved; existing documents should
    use :func:`save_creature_merged` when comment retention matters.
    """
    y = _yaml()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        y.dump(data, f)
    tmp.replace(path)


def save_creature_merged(path: Path, incoming: dict) -> None:
    """Merge a patch into a creature config while preserving comments.

    Mappings merge recursively, while lists and scalars are replaced. Missing
    files begin as empty round-trip mappings.
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
        # Omitted keys remain because ``incoming`` is a patch; full replacement
        # uses ``save_creature_file``.
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
        # List identity is section-dependent, so callers provide the complete
        # replacement list rather than relying on positional or name-based merges.
        target[:] = incoming
