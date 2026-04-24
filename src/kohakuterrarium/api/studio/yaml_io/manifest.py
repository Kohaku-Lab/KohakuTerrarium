"""Round-trip YAML IO for workspace ``kohaku.yaml`` manifests.

Same ruamel round-trip discipline as ``creature.py`` — when a studio
user asks us to sync a newly-scaffolded module into the manifest, we
must not clobber existing comments / formatting / key order. The
public surface here is kept deliberately small: load + idempotent
entry append.
"""

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 120
    y.preserve_quotes = True
    return y


def load_manifest(path: Path) -> Any:
    """Load ``kohaku.yaml`` preserving comments + order.

    Returns a fresh ``CommentedMap`` when the file doesn't exist,
    letting callers append entries into an empty document.
    """
    if not path.exists():
        return CommentedMap()
    y = _yaml()
    with path.open("r", encoding="utf-8") as f:
        data = y.load(f)
    return data if data is not None else CommentedMap()


def save_manifest(path: Path, data: Any) -> None:
    """Write the manifest document back to disk atomically.

    Uses a sibling ``.tmp`` file + atomic rename so a crash mid-dump
    cannot leave a truncated manifest behind.
    """
    y = _yaml()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        y.dump(data, f)
    tmp.replace(path)


def ensure_list(doc: Any, key: str) -> CommentedSeq:
    """Return the ``doc[key]`` list, creating a fresh CommentedSeq if missing.

    Deliberately not a get-or-default — we mutate the doc in place so
    round-trip formatting for other keys is left untouched.
    """
    current = doc.get(key)
    if isinstance(current, CommentedSeq):
        return current
    if isinstance(current, list):
        # Coerce plain lists into CommentedSeq so subsequent appends
        # keep round-trip support.
        seq = CommentedSeq(current)
        doc[key] = seq
        return seq
    seq = CommentedSeq()
    doc[key] = seq
    return seq


def entry_by_name(seq: CommentedSeq, name: str) -> dict | None:
    """Linear lookup — the manifest lists are small enough that this
    beats building a by-name index."""
    for item in seq:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def append_entry(seq: CommentedSeq, entry: dict) -> None:
    """Append *entry* as a ``CommentedMap`` so ruamel preserves its
    key order on subsequent saves."""
    wrapped = CommentedMap()
    for k, v in entry.items():
        wrapped[k] = v
    seq.append(wrapped)
