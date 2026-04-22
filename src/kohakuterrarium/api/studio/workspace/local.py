"""Local filesystem workspace implementation.

Reads from and writes to ``<root>/creatures/**`` and
``<root>/modules/**``. Safe path handling enforced via
``utils.paths.sanitize_name`` + ``ensure_in_root``.

Public surface: matches the ``Workspace`` Protocol in
``workspace/base.py``. Phase 1 implements the read side; Phase 2
adds creature writes; Phase 3 wires module codegen-backed writes.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kohakuterrarium.api.studio.catalog_sources import (
    dedupe_preserve_order,
    package_entries,
    workspace_manifest_entries,
)
from kohakuterrarium.api.studio.utils.paths import (
    ensure_in_root,
    sanitize_name,
)
from kohakuterrarium.api.studio.yaml_io.creature import (
    load_creature_file,
    save_creature_merged,
)
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

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

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
        """Return the top-level summary for the frontend dashboard.

        The ``modules`` field merges three sources so the dashboard
        can surface everything a creature can wire in:

        1. Workspace-authored files under ``<root>/modules/<kind>/``
           (the editable set — REST endpoints operate on these).
        2. Entries declared in the workspace's ``kohaku.yaml`` manifest.
        3. Entries contributed by every installed kt package.

        Each entry carries ``source`` (``workspace`` /
        ``workspace-manifest`` / ``package:<name>``). The REST
        ``GET /api/studio/modules/{kind}`` endpoint still returns only
        workspace-authored files — that surface is for editing.
        """
        modules = {kind: self._modules_summary(kind) for kind in KNOWN_KINDS}
        return {
            "root": self.root,
            "creatures": self.list_creatures(),
            "modules": modules,
        }

    def _modules_summary(self, kind: str) -> list[dict]:
        """Merged module list for the dashboard — files + manifest + packages."""
        merged: list[dict] = []
        for item in self.list_modules(kind):
            merged.append({**item, "source": "workspace"})
        merged.extend(workspace_manifest_entries(self, kind))
        merged.extend(package_entries(kind))
        return dedupe_preserve_order(merged)

    # ------------------------------------------------------------------
    # Creatures — read
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
        effective = _compute_effective(cfg_path, data)
        return {
            "name": name,
            "path": str(creature_dir),
            "config": _coerce_plain(data),
            "prompts": prompts,
            "effective": effective,
        }

    # ------------------------------------------------------------------
    # Creatures — write (Phase 2)
    # ------------------------------------------------------------------

    def scaffold_creature(self, name: str, base: str | None) -> dict:
        name = sanitize_name(name)
        creature_dir = self.creatures_dir / name
        if creature_dir.exists():
            raise FileExistsError(name)
        creature_dir.mkdir(parents=True)
        prompts_dir = creature_dir / "prompts"
        prompts_dir.mkdir()
        # Seed system.md + config.yaml from templates
        from kohakuterrarium.api.studio.templates_render import (  # noqa: E402
            render_creature_config,
            render_system_prompt,
        )

        (prompts_dir / "system.md").write_text(
            render_system_prompt(name), encoding="utf-8"
        )
        cfg_text = render_creature_config(name=name, base=base)
        (creature_dir / "config.yaml").write_text(cfg_text, encoding="utf-8")
        return self.load_creature(name)

    def save_creature(self, name: str, body: dict) -> dict:
        name = sanitize_name(name)
        creature_dir = self.creatures_dir / name
        creature_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = creature_dir / "config.yaml"
        config = body.get("config") or {}
        save_creature_merged(cfg_path, config)
        prompts = body.get("prompts") or {}
        for rel, content in prompts.items():
            target = ensure_in_root(creature_dir, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return self.load_creature(name)

    def delete_creature(self, name: str) -> None:
        name = sanitize_name(name)
        creature_dir = self.creatures_dir / name
        if not creature_dir.exists():
            raise FileNotFoundError(name)
        _rmtree(creature_dir)

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
        creature = sanitize_name(creature)
        creature_dir = self.creatures_dir / creature
        creature_dir.mkdir(parents=True, exist_ok=True)
        target = ensure_in_root(creature_dir, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

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
                # Stem == name; for .py modules the class name is
                # discovered when the file is loaded.
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
        kind_dir = self.module_kind_dir(kind)
        path = kind_dir / f"{name}.py"
        if not path.exists():
            # YAML fallback for subagents
            if kind == "subagents":
                for ext in (".yaml", ".yml"):
                    candidate = kind_dir / f"{name}{ext}"
                    if candidate.exists():
                        path = candidate
                        break
            if not path.exists():
                raise FileNotFoundError(f"{kind}/{name}")

        raw = path.read_text(encoding="utf-8")
        # Parse back to form state via the per-kind codegen
        from kohakuterrarium.api.studio.codegen import get_codegen

        cg = get_codegen(kind)
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
        name = sanitize_name(name)
        kind_dir = self.module_kind_dir(kind)
        kind_dir.mkdir(parents=True, exist_ok=True)
        path = kind_dir / f"{name}.py"
        if path.exists():
            raise FileExistsError(f"{kind}/{name}")

        from kohakuterrarium.api.studio.codegen import get_codegen

        cg = get_codegen(kind)
        # ``kind`` is passed through so io_mod can tell input vs output.
        # Singular form ("input" / "output") matches the template filename.
        singular = kind[:-1] if kind.endswith("s") else kind
        source = cg.render_new(
            {
                "name": name,
                "template": template,
                "kind": singular,
            }
        )
        path.write_text(source, encoding="utf-8")
        return self.load_module(kind, name)

    def save_module(self, kind: str, name: str, data: dict) -> dict:
        name = sanitize_name(name)
        kind_dir = self.module_kind_dir(kind)
        kind_dir.mkdir(parents=True, exist_ok=True)
        path = kind_dir / f"{name}.py"

        from kohakuterrarium.api.studio.codegen import (
            RoundTripError,
            get_codegen,
        )

        cg = get_codegen(kind)

        mode = data.get("mode", "simple")
        if mode == "raw":
            raw = data.get("raw_source", "")
            if not raw:
                raise ValueError("raw_source required in raw mode")
            path.write_text(raw, encoding="utf-8")
        elif mode == "simple":
            form = data.get("form") or {}
            exec_body = data.get("execute_body", "")
            if path.exists():
                try:
                    new_src = cg.update_existing(
                        path.read_text(encoding="utf-8"), form, exec_body
                    )
                except RoundTripError:
                    raise
            else:
                new_src = cg.render_new(
                    {
                        **form,
                        "name": name,
                        "execute_body": exec_body,
                    }
                )
            path.write_text(new_src, encoding="utf-8")
        else:
            raise ValueError(f"unknown mode: {mode!r}")

        return self.load_module(kind, name)

    def delete_module(self, kind: str, name: str) -> None:
        name = sanitize_name(name)
        kind_dir = self.module_kind_dir(kind)
        # Try .py, .yaml, .yml
        for ext in (".py", ".yaml", ".yml"):
            candidate = kind_dir / f"{name}{ext}"
            if candidate.exists():
                candidate.unlink()
                return
        raise FileNotFoundError(f"{kind}/{name}")


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
    """Return {relative_path: content} for every file in prompts/."""
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


def _compute_effective(cfg_path: Path, data: dict) -> dict:
    """Compute the post-inheritance effective config summary.

    Calls the core config loader in a best-effort way — if it
    fails (missing package, broken base ref, …), returns an
    ``error`` key instead of crashing the read.
    """
    try:
        from kohakuterrarium.core.config import load_agent_config
    except Exception as e:
        return {"error": f"core config loader unavailable: {e}"}

    try:
        cfg = load_agent_config(cfg_path.parent)
    except Exception as e:
        return {"error": str(e)}

    # Chain reconstruction from raw data (core doesn't expose the
    # chain as a field — we re-walk it for display)
    chain: list[str] = []
    cur = data
    seen: set[str] = set()
    max_depth = 16
    while max_depth > 0:
        base = cur.get("base_config") if isinstance(cur, dict) else None
        if not base:
            break
        if base in seen:
            break
        seen.add(base)
        chain.append(base)
        # We don't follow further — just surface the first hop is
        # usually enough for the UI. Phase 4 can extend this if
        # needed.
        break

    tools = [t.name for t in cfg.tools] if cfg.tools else []
    subagents = [s.name for s in cfg.subagents] if cfg.subagents else []
    return {
        "model": cfg.model or cfg.llm_profile or "",
        "tools": tools,
        "subagents": subagents,
        "inheritance_chain": chain,
    }


def _coerce_plain(obj: Any) -> Any:
    """Recursively convert ruamel CommentedMap/Seq to plain dict/list.

    JSON serialization blows up on CommentedMap via FastAPI's
    default encoder — coerce on the way out.
    """
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    if isinstance(obj, CommentedMap) or isinstance(obj, dict):
        return {k: _coerce_plain(v) for k, v in obj.items()}
    if isinstance(obj, CommentedSeq) or isinstance(obj, list):
        return [_coerce_plain(v) for v in obj]
    return obj


def _rmtree(path: Path) -> None:
    """Recursive delete — stdlib shutil.rmtree wrapped for atomicity."""
    import shutil

    shutil.rmtree(path)
