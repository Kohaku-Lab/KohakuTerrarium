"""Creature CRUD primitives (scaffold / save / delete / write_prompt).

Operates on a workspace's creature directory. ``LocalWorkspace`` delegates
filesystem mutation here and handles higher-level loading and response shaping.
"""

import shutil
from pathlib import Path

from kohakuterrarium.studio.catalog.packages_scan import package_ref
from kohakuterrarium.studio.editors.starters import starter_form
from kohakuterrarium.studio.editors.templates import (
    render_creature_config,
    render_string,
    render_system_prompt,
)
from kohakuterrarium.studio.editors.utils_paths import ensure_in_root, sanitize_name
from kohakuterrarium.studio.editors.yaml_creature import (
    load_creature_file,
    merge_yaml_text,
    save_creature_merged,
)


def scaffold_creature(
    creatures_dir: Path,
    name: str,
    base: str | None,
    *,
    starter: str | None = None,
    description: str = "",
    purpose: str = "",
    model: str = "",
) -> Path:
    """Create a creature directory with seed configuration and system prompt.

    ``starter`` picks a creature starter (its config and prompt seed); with a
    ``base`` the creature extends it instead, so only ``purpose`` seeds a
    prompt that is appended to the base's. ``model`` sets ``controller.llm``.
    Existing creature directories raise ``FileExistsError``.
    """
    name = sanitize_name(name)
    creature_dir = creatures_dir / name
    if creature_dir.exists():
        raise FileExistsError(name)
    files = render_creature(
        name,
        base,
        starter=starter,
        description=description,
        purpose=purpose,
        model=model,
    )
    for rel, text in files.items():
        target = creature_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return creature_dir


def render_creature(
    name: str,
    base: str | None,
    *,
    starter: str | None = None,
    description: str = "",
    purpose: str = "",
    model: str = "",
) -> dict[str, str]:
    """The files (relative path → text) a new creature gets; nothing is written."""
    name = sanitize_name(name)
    form = {} if base and not starter else starter_form("creatures", starter)
    if form.get("prompt"):
        prompt = render_string(form["prompt"], name=name, purpose=purpose.strip())
    else:
        prompt = render_system_prompt(name, purpose.strip())
    patch = dict(form.get("config") or {})
    config = render_creature_config(
        name=name,
        base=base,
        description=description,
        model=model,
        tools=patch.pop("tools", None),
        subagents=patch.pop("subagents", None),
    )
    if patch:
        config = merge_yaml_text(config, patch)
    return {"config.yaml": config, "prompts/system.md": prompt}


def fork_creature(creatures_dir: Path, name: str, source: Path) -> Path:
    """Copy the creature folder ``source`` into the workspace as ``name``.

    A relative ``base_config`` would break once moved, so it is rewritten to
    the reference (``@pkg/...``) or absolute path of what it pointed at.
    """
    name = sanitize_name(name)
    creature_dir = creatures_dir / name
    if creature_dir.exists():
        raise FileExistsError(name)
    if not source.is_dir():
        raise FileNotFoundError(str(source))
    creatures_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source, creature_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    cfg_path = creature_dir / (
        "config.yaml" if (creature_dir / "config.yaml").exists() else "config.yml"
    )
    if not cfg_path.exists():
        shutil.rmtree(creature_dir)
        raise FileNotFoundError(f"{source} has no config.yaml")
    patch: dict = {"name": name}
    base = load_creature_file(cfg_path).get("base_config")
    if isinstance(base, str) and base and not base.startswith("@"):
        target = (source / base).resolve()
        if not Path(base).is_absolute() and target.exists():
            patch["base_config"] = package_ref(target) or str(target)
    save_creature_merged(cfg_path, patch)
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
