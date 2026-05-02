"""Canonical creature/terrarium catalog scanner.

This module is the single source of truth for "what creatures and
terrariums are visible to this kt install?". Phase 1 of the
studio-cleanup refactor (P1) collapses four previously-duplicated
scanners into ``scan_catalog()``:

- ``cli.packages.list_cli`` — printed package summary + local creatures.
- ``api.routes.registry._scan_all_configs`` — installed packages +
  local cwd creatures/terrariums for the registry browser.
- ``api.routes.configs._scan_creature_configs`` /
  ``_scan_terrarium_configs`` — configured base dirs only.
- ``api.studio.routes.packages`` — installed packages summary.

All four readers project from ``CatalogEntry`` instances produced
here. The dataclass intentionally carries every field any caller has
historically wanted; projection helpers below produce the legacy
shapes 1:1 so HTTP / CLI behavior is preserved verbatim.

Scanning is two-phase:

1. ``list_packages()`` from the low-tier ``packages/`` library walks
   every installed kt package (handles ``.link`` editable installs).
   For each package, every entry under ``creatures/`` and
   ``terrariums/`` is parsed.
2. Optional ``base_dirs`` (e.g. ``cwd/creatures``) are scanned the
   same way and tagged ``source="local"``.

Path-deduplication uses the resolved absolute path of the config
directory, so a package + local-symlink combo never produces two
entries.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.packages.locations import PACKAGES_DIR, get_package_root
from kohakuterrarium.packages.walk import list_packages

# In-memory TTL cache for the configured-base-dir scanners. The
# Dashboard's "+ Creature" / "+ Terrarium" modals call these on every
# open; without the cache, every click re-reads + re-parses every
# YAML on disk. 10s is short enough that newly-scaffolded creatures
# show up promptly without forcing the user to refresh.
_SCAN_CACHE_TTL = 10.0
_creatures_cache: tuple[list[dict], float, tuple[str, ...]] | None = None
_terrariums_cache: tuple[list[dict], float, tuple[str, ...]] | None = None


def _cache_key(base_dirs: list[Path]) -> tuple[str, ...]:
    return tuple(str(p) for p in base_dirs)


def invalidate_scan_caches() -> None:
    """Force the next ``scan_*_in_dirs`` call to re-read disk.

    Call after creating / deleting / renaming a creature or terrarium
    on disk so the catalog reflects the change immediately.
    """
    global _creatures_cache, _terrariums_cache
    _creatures_cache = None
    _terrariums_cache = None


@dataclass
class CatalogEntry:
    """A single discovered creature or terrarium.

    Carries every field historically projected by either the legacy
    ``/api/registry``, ``/api/configs/*``, ``/api/studio/packages*``,
    or the CLI ``kt list`` formatter.
    """

    name: str
    type: str  # "creature" | "terrarium"
    path: Path
    description: str = ""
    model: str = ""
    tools: list[str] = field(default_factory=list)
    creatures: list[str] = field(default_factory=list)  # terrarium-only
    source: str = ""  # package name or "local"

    def as_registry_dict(self) -> dict:
        """Shape historically returned by ``/api/registry``.

        ``_scan_all_configs`` callers expect this shape: rich detail
        plus a ``source`` field tagging the origin.
        """
        d: dict = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "model": self.model,
            "tools": list(self.tools),
            "path": str(self.path),
            "source": self.source,
        }
        if self.type == "terrarium":
            d["creatures"] = list(self.creatures)
        return d


def _build_package_root_map() -> dict[str, str]:
    """Return ``{resolved_pkg_root_str: pkg_name}`` for installed packages.

    Used by ``_to_ref`` to convert a creature/terrarium absolute path
    back into an ``@package/...`` reference.
    """
    mapping: dict[str, str] = {}
    if not PACKAGES_DIR.exists():
        return mapping
    for pkg in list_packages():
        pkg_root = get_package_root(pkg["name"])
        if pkg_root is not None:
            mapping[str(pkg_root.resolve())] = pkg["name"]
    return mapping


def to_ref(path: Path, package_roots: dict[str, str]) -> str:
    """Convert absolute ``path`` to ``@pkg/...`` if inside a package.

    Returns the absolute path string for paths outside any package.
    Mirrors ``api.routes.configs._to_ref`` byte-for-byte so the URL
    payload layout is unchanged.
    """
    resolved = str(path.resolve())
    for root, name in package_roots.items():
        if resolved.startswith(root):
            rel = resolved[len(root) :].lstrip("/").lstrip("\\").replace("\\", "/")
            return f"@{name}/{rel}"
    return str(path)


# ---------------------------------------------------------------------------
# Per-config parsing — kept verbatim from the four legacy scanners.
# ---------------------------------------------------------------------------


def _parse_creature_detail(config_dir: Path) -> CatalogEntry | None:
    """Parse a creature config dir and return a ``CatalogEntry`` or ``None``.

    Verbatim port of ``api.routes.registry._parse_creature_detail`` —
    full ``load_agent_config`` first, fallback to raw YAML if that
    fails.
    """
    config_file = config_dir / "config.yaml"
    if not config_file.exists():
        config_file = config_dir / "config.yml"
    if not config_file.exists():
        return None

    try:
        cfg = load_agent_config(config_dir)
        tools_list = [t.name for t in cfg.tools]
        return CatalogEntry(
            name=cfg.name,
            type="creature",
            path=config_dir,
            description=getattr(cfg, "system_prompt", "")[:200],
            model=cfg.model,
            tools=tools_list,
        )
    except Exception as e:
        # Fallback: parse raw YAML for basic info.
        _ = e  # full config parse failed, try raw YAML
        try:
            data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            return CatalogEntry(
                name=data.get("name", config_dir.name),
                type="creature",
                path=config_dir,
                description=data.get("description", ""),
                model=data.get("model", data.get("controller", {}).get("model", "")),
                tools=[
                    t.get("name", "")
                    for t in data.get("tools", [])
                    if isinstance(t, dict)
                ],
            )
        except Exception as e:
            _ = e  # config unreadable, skip
            return None


def _parse_terrarium_detail(config_dir: Path) -> CatalogEntry | None:
    """Parse a terrarium config dir and return a ``CatalogEntry`` or ``None``.

    Verbatim port of ``api.routes.registry._parse_terrarium_detail``.
    """
    config_file = config_dir / "terrarium.yaml"
    if not config_file.exists():
        config_file = config_dir / "terrarium.yml"
    if not config_file.exists():
        return None

    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        terrarium = data.get("terrarium", data)
        creatures = terrarium.get("creatures", [])
        creature_names = [c.get("name", "") for c in creatures if isinstance(c, dict)]
        return CatalogEntry(
            name=terrarium.get("name", config_dir.name),
            type="terrarium",
            path=config_dir,
            description=terrarium.get("description", ""),
            creatures=creature_names,
        )
    except Exception as e:
        _ = e  # config unreadable, skip
        return None


def _parse_creature_minimal(config_dir: Path) -> dict:
    """Raw-YAML-only creature parse used by ``api.routes.configs``.

    Returns a minimal ``{name, description}`` dict (no model/tools);
    the configs route historically does not load the full agent
    config, only the raw YAML. Falls back to the directory name on
    parse failure (also verbatim from the legacy scanner).
    """
    config_file = config_dir / "config.yaml"
    if not config_file.exists():
        config_file = config_dir / "config.yml"
    if not config_file.exists():
        return {"name": config_dir.name, "description": ""}
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        return {
            "name": data.get("name", config_dir.name),
            "description": data.get("description", ""),
        }
    except Exception as e:
        _ = e  # fallback: creature config parse failed, return minimal entry
        return {"name": config_dir.name, "description": ""}


def _parse_terrarium_minimal(config_dir: Path) -> dict:
    """Raw-YAML-only terrarium parse used by ``api.routes.configs``."""
    config_file = config_dir / "terrarium.yaml"
    if not config_file.exists():
        config_file = config_dir / "terrarium.yml"
    if not config_file.exists():
        return {"name": config_dir.name, "description": ""}
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        terrarium = data.get("terrarium", data)
        return {
            "name": terrarium.get("name", config_dir.name),
            "description": terrarium.get("description", ""),
        }
    except Exception as e:
        _ = e  # fallback: terrarium config parse failed, return minimal entry
        return {"name": config_dir.name, "description": ""}


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------


def scan_catalog() -> list[CatalogEntry]:
    """Return every visible creature + terrarium across packages and cwd.

    Verbatim merge of:
    - ``list_packages()`` (handles ``.link`` editable installs).
    - ``cwd/creatures`` and ``cwd/terrariums``.

    Resolved paths are de-duplicated, so a package + symlinked-local
    pair never produces two entries. Entries with empty ``name``
    (broken configs) are filtered, matching the legacy behavior.
    """
    results: list[CatalogEntry] = []
    seen_paths: set[str] = set()

    def _add_creature(config_dir: Path, source: str = "") -> None:
        key = str(config_dir.resolve())
        if key in seen_paths:
            return
        seen_paths.add(key)
        entry = _parse_creature_detail(config_dir)
        if entry is not None:
            entry.source = source
            results.append(entry)

    def _add_terrarium(config_dir: Path, source: str = "") -> None:
        key = str(config_dir.resolve())
        if key in seen_paths:
            return
        seen_paths.add(key)
        entry = _parse_terrarium_detail(config_dir)
        if entry is not None:
            entry.source = source
            results.append(entry)

    # Scan installed packages (handles .link editable installs)
    for pkg in list_packages():
        pkg_path = Path(pkg["path"])
        pkg_name = pkg["name"]
        for c in pkg.get("creatures", []):
            _add_creature(pkg_path / c["path"], source=pkg_name)
        for t in pkg.get("terrariums", []):
            _add_terrarium(pkg_path / t["path"], source=pkg_name)

    # Scan local project directories
    cwd = Path.cwd()
    for creatures_dir in [cwd / "creatures"]:
        if creatures_dir.is_dir():
            for child in sorted(creatures_dir.iterdir()):
                if child.is_dir():
                    _add_creature(child, source="local")

    for terrariums_dir in [cwd / "terrariums"]:
        if terrariums_dir.is_dir():
            for child in sorted(terrariums_dir.iterdir()):
                if child.is_dir():
                    _add_terrarium(child, source="local")

    # Filter out entries with empty names (broken configs)
    return [r for r in results if r.name]


def scan_creatures_in_dirs(base_dirs: list[Path]) -> list[dict]:
    """Scan configured base dirs for creature configs (raw-YAML only).

    Used by the ``/api/configs/creatures`` endpoint. Returns the
    legacy ``{name, path, description}`` shape; ``path`` is rendered
    as an ``@pkg/...`` ref when the directory lives inside a package,
    matching ``api.routes.configs._to_ref``.

    Cached for ``_SCAN_CACHE_TTL`` seconds keyed by the resolved
    base-dir tuple — the v2 dashboard quick-start modal otherwise pays
    the YAML-parse cost every time it opens. Use
    :func:`invalidate_scan_caches` after creating / deleting a creature
    on disk to force a re-scan.
    """
    global _creatures_cache
    key = _cache_key(base_dirs)
    now = time.time()
    if _creatures_cache is not None:
        cached_results, cached_at, cached_key = _creatures_cache
        if cached_key == key and now - cached_at < _SCAN_CACHE_TTL:
            return cached_results

    results: list[dict] = []
    package_roots = _build_package_root_map()
    for base_dir in base_dirs:
        if not base_dir.is_dir():
            continue
        for child in sorted(base_dir.iterdir()):
            if not child.is_dir():
                continue
            config_file = child / "config.yaml"
            if not config_file.exists():
                config_file = child / "config.yml"
            if not config_file.exists():
                continue
            minimal = _parse_creature_minimal(child)
            results.append(
                {
                    "name": minimal["name"],
                    "path": to_ref(child, package_roots),
                    "description": minimal["description"],
                }
            )
    _creatures_cache = (results, now, key)
    return results


def scan_terrariums_in_dirs(base_dirs: list[Path]) -> list[dict]:
    """Scan configured base dirs for terrarium configs (raw-YAML only).

    Cached for ``_SCAN_CACHE_TTL`` seconds (see
    :func:`scan_creatures_in_dirs` for caching rationale).
    """
    global _terrariums_cache
    key = _cache_key(base_dirs)
    now = time.time()
    if _terrariums_cache is not None:
        cached_results, cached_at, cached_key = _terrariums_cache
        if cached_key == key and now - cached_at < _SCAN_CACHE_TTL:
            return cached_results

    results: list[dict] = []
    package_roots = _build_package_root_map()
    for base_dir in base_dirs:
        if not base_dir.is_dir():
            continue
        for child in sorted(base_dir.iterdir()):
            if not child.is_dir():
                continue
            config_file = child / "terrarium.yaml"
            if not config_file.exists():
                config_file = child / "terrarium.yml"
            if not config_file.exists():
                continue
            minimal = _parse_terrarium_minimal(child)
            results.append(
                {
                    "name": minimal["name"],
                    "path": to_ref(child, package_roots),
                    "description": minimal["description"],
                }
            )
    _terrariums_cache = (results, now, key)
    return results


def dedupe_dirs(dirs: list[str]) -> list[Path]:
    """Resolve + deduplicate a list of directory paths.

    Mirrors the dedup logic in ``api.routes.configs.set_config_dirs``.
    """
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        p = Path(d).resolve()
        key = str(p)
        if key not in seen:
            out.append(p)
            seen.add(key)
    return out
