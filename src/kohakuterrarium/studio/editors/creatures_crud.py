"""Creature CRUD primitives (scaffold / save / delete / write_prompt).

Operates on a workspace's creature directory. ``LocalWorkspace`` delegates
filesystem mutation here and handles higher-level loading and response shaping.
"""

import shutil
from pathlib import Path

from kohakuterrarium.studio.editors.templates import (
    render_creature_config,
    render_system_prompt,
)
from kohakuterrarium.studio.editors.utils_paths import ensure_in_root, sanitize_name
from kohakuterrarium.studio.editors.yaml_creature import save_creature_merged


def scaffold_creature(creatures_dir: Path, name: str, base: str | None) -> Path:
    """Create a creature directory with seed configuration and system prompt.

    Existing creature directories raise ``FileExistsError``.
    """
    name = sanitize_name(name)
    creature_dir = creatures_dir / name
    if creature_dir.exists():
        raise FileExistsError(name)
    creature_dir.mkdir(parents=True)
    prompts_dir = creature_dir / "prompts"
    prompts_dir.mkdir()
    # Both seed files must come from the shared templates to remain consistent.
    (prompts_dir / "system.md").write_text(render_system_prompt(name), encoding="utf-8")
    cfg_text = render_creature_config(name=name, base=base)
    (creature_dir / "config.yaml").write_text(cfg_text, encoding="utf-8")
    return creature_dir


def save_creature(creatures_dir: Path, name: str, body: dict) -> Path:
    """Merge a creature config and write any supplied prompt files.

    Prompt paths are resolved within the creature directory. The creature
    directory is returned after persistence.
    """
    name = sanitize_name(name)
    creature_dir = creatures_dir / name
    creature_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = creature_dir / "config.yaml"
    config = body.get("config") or {}
    save_creature_merged(cfg_path, config)
    prompts = body.get("prompts") or {}
    for rel, content in prompts.items():
        target = ensure_in_root(creature_dir, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return creature_dir


def delete_creature(creatures_dir: Path, name: str) -> None:
    """Recursively delete a creature, raising ``FileNotFoundError`` if absent."""
    name = sanitize_name(name)
    creature_dir = creatures_dir / name
    if not creature_dir.exists():
        raise FileNotFoundError(name)
    shutil.rmtree(creature_dir)


def write_prompt(creatures_dir: Path, creature: str, rel: str, body: str) -> None:
    """Write one prompt file while enforcing creature-directory containment."""
    creature = sanitize_name(creature)
    creature_dir = creatures_dir / creature
    creature_dir.mkdir(parents=True, exist_ok=True)
    target = ensure_in_root(creature_dir, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
