"""Local filesystem workspace implementation.

Reads from and writes to ``<root>/creatures/**`` and
``<root>/modules/**``. Safe path handling enforced via
``utils_paths.sanitize_name`` + ``ensure_in_root``.

Public surface: matches the ``Workspace`` Protocol in
``workspace_manifest.py``. Manifest sync, sidecar IO, codegen-driven
module writes, and the post-inheritance "effective config"
projection are delegated to sibling modules so this file stays small.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from kohakuterrarium.studio.editors import creatures_crud, modules_crud
from kohakuterrarium.studio.editors.codegen_init import get_codegen
from kohakuterrarium.studio.editors.utils_paths import ensure_in_root, sanitize_name
from kohakuterrarium.studio.editors.workspace_manifest import (
    compute_effective,
    find_module_file,
    load_sidecar_doc,
    modules_summary,
    read_sidecar_schema,
    sync_manifest_entry,
)
from kohakuterrarium.studio.editors.yaml_creature import load_creature_file
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


KNOWN_KINDS = ("tools", "subagents", "triggers", "plugins", "inputs", "outputs")


@dataclass
class LocalWorkspace:
    """Filesystem-backed workspace.

    Constructed via ``open(path)`` — validates the path exists and
    is a directory. Subdirectories (``creatures/``, ``modules/``)
    are created lazily on first save.
    """

    root_path: Path

    @classmethod
    def open(cls, root: str | Path) -> "LocalWorkspace":
        p = Path(root).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"workspace not found: {p}")
        if not p.is_dir():
            raise NotADirectoryError(str(p))
        return cls(root_path=p)

    @property
    def root(self) -> str:
        return str(self.root_path)

    @property
    def creatures_dir(self) -> Path:
        return self.root_path / "creatures"

    @property
    def modules_dir(self) -> Path:
        return self.root_path / "modules"

    def module_kind_dir(self, kind: str) -> Path:
        if kind not in KNOWN_KINDS:
            raise ValueError(f"unknown module kind: {kind!r}")
        return self.modules_dir / kind

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        return {
            "root": self.root,
            "creatures": self.list_creatures(),
            "modules": {
                kind: modules_summary(self, kind, self.list_modules(kind))
                for kind in KNOWN_KINDS
            },
        }

    # ------------------------------------------------------------------
    # Creatures
    # ------------------------------------------------------------------

    def list_creatures(self) -> list[dict]:
        if not self.creatures_dir.is_dir():
            return []
        results: list[dict] = []
        for child in sorted(self.creatures_dir.iterdir()):
            if not child.is_dir():
                continue
            cfg = _find_config_file(child)
            if cfg is None:
                continue
            try:
                data = load_creature_file(cfg)
            except Exception as e:
                logger.warning(
                    "creature config parse failed", path=str(cfg), error=str(e)
                )
                results.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "description": "",
                        "base_config": None,
                        "error": f"parse failed: {e}",
                    }
                )
                continue
            results.append(
                {
                    "name": data.get("name", child.name),
                    "path": str(child),
                    "description": data.get("description", ""),
                    "base_config": data.get("base_config"),
                }
            )
        return results

    def load_creature(self, name: str) -> dict:
        name = sanitize_name(name)
        creature_dir = self.creatures_dir / name
        cfg_path = _find_config_file(creature_dir)
        if cfg_path is None:
            raise FileNotFoundError(name)
        data = load_creature_file(cfg_path)
        prompts = _collect_prompts(creature_dir)
        return {
            "name": name,
            "path": str(creature_dir),
            "config": _coerce_plain(data),
            "prompts": prompts,
            "effective": compute_effective(cfg_path, data),
        }

    def scaffold_creature(self, name: str, base: str | None) -> dict:
        creatures_crud.scaffold_creature(self.creatures_dir, name, base)
        return self.load_creature(name)

    def save_creature(self, name: str, body: dict) -> dict:
        creatures_crud.save_creature(self.creatures_dir, name, body)
        return self.load_creature(name)

    def delete_creature(self, name: str) -> None:
        creatures_crud.delete_creature(self.creatures_dir, name)

    def read_prompt(self, creature: str, rel: str) -> str:
        creature = sanitize_name(creature)
        creature_dir = self.creatures_dir / creature
        if not creature_dir.is_dir():
            raise FileNotFoundError(creature)
        target = ensure_in_root(creature_dir, rel)
        if not target.exists():
            raise FileNotFoundError(str(target))
        return target.read_text(encoding="utf-8")

    def write_prompt(self, creature: str, rel: str, body: str) -> None:
        creatures_crud.write_prompt(self.creatures_dir, creature, rel, body)

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------

    def list_modules(self, kind: str) -> list[dict]:
        kind_dir = self.module_kind_dir(kind)
        if not kind_dir.is_dir():
            return []
        results: list[dict] = []
        for child in sorted(kind_dir.iterdir()):
            if child.is_file() and child.suffix in (".py", ".yaml", ".yml"):
                results.append(
                    {
                        "kind": kind,
                        "name": child.stem,
                        "path": str(child.relative_to(self.root_path)).replace(
                            "\\", "/"
                        ),
                    }
                )
        return results

    def load_module(self, kind: str, name: str) -> dict:
        name = sanitize_name(name)
        path = self._find_module_file(kind, name)
        if path is None:
            raise FileNotFoundError(f"{kind}/{name}")

        raw = path.read_text(encoding="utf-8")
        cg = get_codegen(kind)
        if kind == "plugins":
            sidecar_schema = read_sidecar_schema(path)
            envelope = cg.parse_back(raw, sidecar_schema=sidecar_schema)
        else:
            envelope = cg.parse_back(raw)
        envelope.update(
            {
                "kind": kind,
                "name": name,
                "path": str(path.relative_to(self.root_path)).replace("\\", "/"),
                "raw_source": raw,
            }
        )
        return envelope

    def scaffold_module(self, kind: str, name: str, template: str | None) -> dict:
        modules_crud.scaffold_module(self.module_kind_dir(kind), kind, name, template)
        return self.load_module(kind, name)

    def save_module(self, kind: str, name: str, data: dict) -> dict:
        kind_dir = self.module_kind_dir(kind)
        existing = self._find_module_file(kind, sanitize_name(name))
        fallback = kind_dir / f"{sanitize_name(name)}.py"
        modules_crud.save_module(
            kind, name, data, existing_path=existing, fallback_path=fallback
        )
        return self.load_module(kind, name)

    def delete_module(self, kind: str, name: str) -> None:
        path = self._find_module_file(kind, sanitize_name(name))
        modules_crud.delete_module(kind, name, path)

    def load_module_doc(self, kind: str, name: str) -> dict:
        name = sanitize_name(name)
        py_path = self._find_module_file(kind, name)
        if py_path is None:
            raise FileNotFoundError(f"{kind}/{name}")
        return load_sidecar_doc(py_path, self.root_path)

    def save_module_doc(self, kind: str, name: str, content: str) -> dict:
        name = sanitize_name(name)
        py_path = self._find_module_file(kind, name)
        if py_path is None:
            raise FileNotFoundError(f"{kind}/{name}")
        modules_crud.save_module_doc(py_path, content)
        return self.load_module_doc(kind, name)

    def sync_manifest(self, kind: str, name: str) -> dict:
        name = sanitize_name(name)
        py_path = self._find_module_file(kind, name)
        if py_path is None:
            raise FileNotFoundError(f"{kind}/{name}")
        return sync_manifest_entry(self.root_path, kind, name, py_path, KNOWN_KINDS)

    def _find_module_file(self, kind: str, name: str) -> Path | None:
        return find_module_file(
            self.root_path, self.module_kind_dir(kind), kind, name, self
        )


# ----------------------------------------------------------------------
# Helpers (module-private)
# ----------------------------------------------------------------------


def _find_config_file(creature_dir: Path) -> Path | None:
    for name in ("config.yaml", "config.yml"):
        p = creature_dir / name
        if p.exists():
            return p
    return None


def _collect_prompts(creature_dir: Path) -> dict[str, str]:
    prompts: dict[str, str] = {}
    prompts_dir = creature_dir / "prompts"
    if not prompts_dir.is_dir():
        return prompts
    for p in sorted(prompts_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".md", ".txt"):
            rel = p.relative_to(creature_dir).as_posix()
            try:
                prompts[rel] = p.read_text(encoding="utf-8")
            except Exception:
                continue
    return prompts


def _coerce_plain(obj: Any) -> Any:
    if isinstance(obj, CommentedMap) or isinstance(obj, dict):
        return {k: _coerce_plain(v) for k, v in obj.items()}
    if isinstance(obj, CommentedSeq) or isinstance(obj, list):
        return [_coerce_plain(v) for v in obj]
    return obj


def _rmtree(path: Path) -> None:
    """Recursive delete — stdlib shutil.rmtree wrapped for atomicity."""
    shutil.rmtree(path)
