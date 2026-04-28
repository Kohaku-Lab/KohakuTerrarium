"""Creature CRUD primitives (scaffold / save / delete / write_prompt).

Operate on a workspace root directly; the surrounding ``LocalWorkspace``
methods are thin wrappers that thread sanitization + load callbacks
through these helpers.
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
    """Create a fresh creature directory + seed config and system.md.

    Returns the new creature directory. Raises ``FileExistsError`` if
    ``<creatures_dir>/<name>/`` already exists.
    """
    name = sanitize_name(name)
    creature_dir = creatures_dir / name
    if creature_dir.exists():
        raise FileExistsError(name)
    creature_dir.mkdir(parents=True)
    prompts_dir = creature_dir / "prompts"
    prompts_dir.mkdir()
    # Seed system.md + config.yaml from templates
    (prompts_dir / "system.md").write_text(render_system_prompt(name), encoding="utf-8")
    cfg_text = render_creature_config(name=name, base=base)
    (creature_dir / "config.yaml").write_text(cfg_text, encoding="utf-8")
    return creature_dir


def save_creature(creatures_dir: Path, name: str, body: dict) -> Path:
    """Write the creature's config.yaml + any prompt files in *body*.

    The incoming ``body`` shape mirrors the API request:
    ``{"config": {...}, "prompts": {"<rel>": "<content>"}}``.
    Returns the creature directory path.
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
    """Recursively delete the creature directory.

    Raises ``FileNotFoundError`` when no such creature exists.
    """
    name = sanitize_name(name)
    creature_dir = creatures_dir / name
    if not creature_dir.exists():
        raise FileNotFoundError(name)
    shutil.rmtree(creature_dir)


def write_prompt(creatures_dir: Path, creature: str, rel: str, body: str) -> None:
    """Write a single prompt file under ``<creatures_dir>/<creature>/<rel>``."""
    creature = sanitize_name(creature)
    creature_dir = creatures_dir / creature
    creature_dir.mkdir(parents=True, exist_ok=True)
    target = ensure_in_root(creature_dir, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
