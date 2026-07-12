"""Pure creature-id helpers (mint / clean / decode).

A leaf module — stdlib only — so both :mod:`terrarium.creature_host` (which
mints ids) and :mod:`terrarium.drive.runtime` (which decodes a persisted id back
to a name for the resume assignee remap, R1-43) can share them without either
dragging the heavy Agent/builtins import graph into the other.
"""

import re
from uuid import uuid4

# A ``_safe_creature_id`` suffix: an underscore + 8 lowercase hex chars.
_ID_SUFFIX_RE = re.compile(r"_[0-9a-f]{8}$")


def _clean_creature_name(name: str) -> str:
    """The id-safe form of a config name (alnum / ``-`` / ``_`` preserved)."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)


def _safe_creature_id(name: str) -> str:
    """Mint a unique creature id from a config name.

    Names from a recipe are usually meaningful and unique within the recipe, but
    the engine namespace is process-wide — append a short random suffix so two
    recipes with the same creature name don't collide.
    """
    cleaned = _clean_creature_name(name)
    return f"{cleaned or 'creature'}_{uuid4().hex[:8]}"


def _decode_creature_name(creature_id: str) -> str:
    """Recover the cleaned config name a :func:`_safe_creature_id` encoded.

    Strips exactly one trailing ``_<8 hex>`` suffix; an id minted some other way
    (an explicit ``creature_id=``) is returned unchanged. Used by resume to match
    a persisted (old-runtime-id) assignee to a resumed creature by name
    (design §6.5 stable-identity remap, R1-43).
    """
    return _ID_SUFFIX_RE.sub("", creature_id)
