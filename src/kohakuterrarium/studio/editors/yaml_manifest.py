"""Round-trip YAML IO for workspace ``kohaku.yaml`` manifests.

Uses ruamel.yaml round-trip mode so synchronizing scaffolded modules preserves
existing comments, formatting, quoting, and key order. The helpers expose only
the loading, saving, and list-entry operations needed by manifest editors.
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
    """Load ``kohaku.yaml`` with round-trip metadata.

    Missing and empty files return a fresh ``CommentedMap`` ready for mutation.
    """
    if not path.exists():
        return CommentedMap()
    y = _yaml()
    with path.open("r", encoding="utf-8") as f:
        data = y.load(f)
    return data if data is not None else CommentedMap()


def save_manifest(path: Path, data: Any) -> None:
    """Atomically persist a manifest through a sibling temporary file."""
    y = _yaml()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        y.dump(data, f)
    tmp.replace(path)


def ensure_list(doc: Any, key: str) -> CommentedSeq:
    """Return ``doc[key]`` as a mutable round-trip sequence.

    Missing or plain-list values are replaced in place without disturbing other
    manifest keys.
    """
    current = doc.get(key)
    if isinstance(current, CommentedSeq):
        return current
    if isinstance(current, list):
        # Convert plain lists so subsequent mutations retain round-trip metadata.
        seq = CommentedSeq(current)
        doc[key] = seq
        return seq
    seq = CommentedSeq()
    doc[key] = seq
    return seq


def entry_by_name(seq: CommentedSeq, name: str) -> dict | None:
    """Return the named mapping from a small manifest sequence."""
    for item in seq:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def append_entry(seq: CommentedSeq, entry: dict) -> None:
    """Append an entry as a ``CommentedMap`` with stable key order."""
    wrapped = CommentedMap()
    for k, v in entry.items():
        wrapped[k] = v
    seq.append(wrapped)
