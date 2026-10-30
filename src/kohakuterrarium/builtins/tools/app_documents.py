"""Instant-app documents: content-addressed storage, patching and the widget spec.

With a session store, each document is a session artifact
(``apps/docs/<sha256>.html``) and ``apps/widgets/<id>.json`` names the current
document of each app, so ``read`` works after a resume. Without a store the
documents live in memory and the spec carries the HTML inline.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.core.tools.output import artifact_served_url
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class PatchError(ValueError):
    """A ``{find, replace}`` edit that does not apply to the current document."""


@dataclass(frozen=True)
class AppDocument:
    """One version of an app's HTML document."""

    html: str
    sha256: str
    url: str = ""
    data: Any = None

    @property
    def size(self) -> int:
        return len(self.html.encode("utf-8"))


def digest(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def apply_patch(html: str, edits: Any) -> str:
    """Apply ``[{find, replace}]`` edits in order; each ``find`` must occur exactly once."""
    if not isinstance(edits, list) or not edits:
        raise PatchError("patch must be a non-empty list of {find, replace} edits")
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise PatchError(f"patch[{index}] must be an object")
        find, replace = edit.get("find"), edit.get("replace", "")
        if not isinstance(find, str) or not find or not isinstance(replace, str):
            raise PatchError(f"patch[{index}] needs a non-empty 'find' and a 'replace'")
        count = html.count(find)
        if count == 0:
            raise PatchError(
                f"patch[{index}] 'find' not found in the app document; it must "
                "match the current document exactly"
            )
        if count > 1:
            raise PatchError(
                f"patch[{index}] 'find' matches {count} places; add surrounding "
                "context so it matches exactly one"
            )
        html = html.replace(find, replace, 1)
    return html


class AppDocumentStore:
    """Stores app documents for one tool instance."""

    def __init__(self) -> None:
        self._memory: dict[str, AppDocument] = {}

    def put(self, html: str, store: Any, data: Any = None) -> AppDocument:
        """Store a document version (content-addressed); return it with its URL."""
        sha = digest(html)
        if store is not None and hasattr(store, "write_artifact"):
            rel = f"apps/docs/{sha}.html"
            try:
                path = store.write_artifact(rel, html.encode("utf-8"))
                url = artifact_served_url(store, rel, path)
                return AppDocument(html=html, sha256=sha, url=url, data=data)
            except Exception as e:
                logger.warning("App document not stored", error=str(e))
        return AppDocument(html=html, sha256=sha, data=data)

    def bind(self, widget_id: str, doc: AppDocument, store: Any) -> None:
        """Make ``doc`` the current document of ``widget_id``."""
        self._memory[widget_id] = doc
        if not doc.url or store is None:
            return
        index = {"sha256": doc.sha256, "data": doc.data}
        try:
            store.write_artifact(
                f"apps/widgets/{widget_id}.json",
                json.dumps(index, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as e:
            logger.warning("App index not stored", error=str(e))

    def load(self, widget_id: str, store: Any) -> AppDocument | None:
        """The current document of ``widget_id``, from memory or the session store."""
        if widget_id in self._memory:
            return self._memory[widget_id]
        base = getattr(store, "artifacts_dir", None) if store is not None else None
        if base is None:
            return None
        try:
            index = json.loads((base / f"apps/widgets/{widget_id}.json").read_text())
            sha = str(index["sha256"])
            rel = f"apps/docs/{sha}.html"
            html = (base / rel).read_text(encoding="utf-8")
        except (OSError, ValueError, KeyError, TypeError):
            return None
        url = artifact_served_url(store, rel, base / rel)
        doc = AppDocument(html=html, sha256=sha, url=url, data=index.get("data"))
        self._memory[widget_id] = doc
        return doc


def app_spec(doc: AppDocument, hosts: tuple[str, ...]) -> dict[str, Any]:
    """The ``kt.app`` widget spec for a document."""
    spec: dict[str, Any] = {
        "sha256": doc.sha256,
        "allowed_hosts": list(hosts),
        "data": doc.data,
    }
    if doc.url:
        spec["document_url"] = doc.url
    else:
        spec["html"] = doc.html
    return spec
