"""Creature read-side primitives (list / load / read_prompt).

Used by the catalog routes when surfacing a workspace's creatures.
The write-side bodies live in ``studio.editors.creatures_crud``.
"""

from kohakuterrarium.studio.editors.utils_paths import (
    UnsafePath,
    ensure_in_root,
    sanitize_name,
)


def list_creatures(ws) -> list[dict]:
    """Return the workspace's creature directory listing."""
    return ws.list_creatures()


def load_creature(ws, name: str) -> dict:
    """Return the full creature envelope (config / prompts / effective)."""
    return ws.load_creature(name)


def read_prompt(ws, creature: str, rel: str) -> str:
    """Return the contents of a prompt file relative to the creature dir."""
    creature = sanitize_name(creature)
    creature_dir = ws.creatures_dir / creature
    if not creature_dir.is_dir():
        raise FileNotFoundError(creature)
    target = ensure_in_root(creature_dir, rel)
    if not target.exists():
        raise FileNotFoundError(str(target))
    return target.read_text(encoding="utf-8")


# Re-export so route code can import the path-error type from one place.
__all__ = ["list_creatures", "load_creature", "read_prompt", "UnsafePath"]
