"""Studio catalog — bundled remote registry reader.

Loads the bundled known-good package index behind a stable catalog boundary so
additional registry sources can be introduced without changing consumers.
"""

import json
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# The bundled registry is rooted at the top-level package rather than the Studio
# subpackage.
_REGISTRY_JSON = Path(__file__).resolve().parent.parent.parent / "registry.json"


def load_remote_registry() -> dict:
    """Return the bundled index with a stable empty fallback shape.

    Missing or malformed package data must not make registry browsing fail.
    """
    if not _REGISTRY_JSON.exists():
        return {"repos": []}
    try:
        return json.loads(_REGISTRY_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to read registry.json", error=str(e))
        return {"repos": []}
