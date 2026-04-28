"""Studio catalog — bundled remote registry reader.

Reads ``src/kohakuterrarium/registry.json`` (the bundled
"known good" remote package index). Future remote-registry sources
will plug in here without callers having to learn a new module
location.
"""

import json
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# ``src/kohakuterrarium/registry.json`` lives next to ``__init__.py``
# of the top-level package (``studio/catalog/`` is two levels deeper).
_REGISTRY_JSON = Path(__file__).resolve().parent.parent.parent / "registry.json"


def load_remote_registry() -> dict:
    """Return the bundled remote-package index.

    Returns ``{"repos": []}`` when the file is missing or unreadable
    so callers always see the same shape. Verbatim port of the body
    of ``api.routes.registry.list_remote``.
    """
    if not _REGISTRY_JSON.exists():
        return {"repos": []}
    try:
        return json.loads(_REGISTRY_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to read registry.json", error=str(e))
        return {"repos": []}
