"""Creature read-side primitives (list / load / read_prompt).

Exposes workspace creature metadata and prompt reads. Mutating operations remain
in the editor layer so catalog access stays read-only.
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
    """Read a prompt while constraining the relative path to its creature root."""
    creature = sanitize_name(creature)
    creature_dir = ws.creatures_dir / creature
    if not creature_dir.is_dir():
        raise FileNotFoundError(creature)
    target = ensure_in_root(creature_dir, rel)
    if not target.exists():
        raise FileNotFoundError(str(target))
    return target.read_text(encoding="utf-8")


# Consumers can handle path-safety failures without depending on editor internals.
__all__ = ["list_creatures", "load_creature", "read_prompt", "UnsafePath"]
