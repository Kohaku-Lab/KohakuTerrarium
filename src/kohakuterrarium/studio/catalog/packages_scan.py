"""Canonical creature/terrarium catalog scanner.

Defines the shared discovery rules for creatures and terrariums visible to the
current installation. Installed package manifests and optional local directories
are projected into common :class:`CatalogEntry` records, from which CLI and HTTP
adapters derive their required payload shapes.

Resolved absolute paths are the identity boundary, preventing editable package
links or overlapping local directories from producing duplicate entries.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.packages.locations import get_package_root, packages_dir
from kohakuterrarium.packages.walk import list_packages

# Repeated dashboard discovery should not reparse every YAML file. A short TTL
# limits that cost while explicit mutation paths invalidate caches immediately.
_SCAN_CACHE_TTL = 10.0
_creatures_cache: tuple[list[dict], float, tuple[str, ...]] | None = None
_terrariums_cache: tuple[list[dict], float, tuple[str, ...]] | None = None


def _cache_key(base_dirs: list[Path]) -> tuple[str, ...]:
    return tuple(str(p) for p in base_dirs)


def invalidate_scan_caches() -> None:
    """Invalidate cached directory scans after catalog-affecting mutations."""
    global _creatures_cache, _terrariums_cache
    _creatures_cache = None
    _terrariums_cache = None


@dataclass
class CatalogEntry:
    """Canonical discovery record from which consumer payloads are projected."""

    name: str
    type: str  # Restricted to the two catalog entity kinds.
    path: Path
    description: str = ""
    model: str = ""
    tools: list[str] = field(default_factory=list)
    creatures: list[str] = field(default_factory=list)  # Populated for terrariums.
    source: str = ""  # Originating package name or the local workspace.

    def as_registry_dict(self) -> dict:
        """Project the rich registry payload, including discovery origin."""
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


def manifest_entry_rel_path(entry, kind: str) -> str | None:
    """Normalize supported manifest entry forms to a relative path.

    Entries may provide an explicit path, a name resolved under the conventional
    kind directory, or a bare string interpreted as either form. Malformed entries
    return ``None`` so one bad declaration cannot invalidate the entire catalog.
    """
    if isinstance(entry, dict):
        rel = entry.get("path")
        if isinstance(rel, str) and rel:
            return rel
        entry_name = entry.get("name")
        if isinstance(entry_name, str) and entry_name:
            return f"{kind}/{entry_name}"
        return None
    if isinstance(entry, str) and entry:
        return entry if "/" in entry else f"{kind}/{entry}"
    return None


def _build_package_root_map() -> dict[str, str]:
    """Map resolved package roots to names for portable reference rendering."""
    mapping: dict[str, str] = {}
    if not packages_dir().exists():
        return mapping
    for pkg in list_packages():
        pkg_root = get_package_root(pkg["name"])
        if pkg_root is not None:
            mapping[str(pkg_root.resolve())] = pkg["name"]
    return mapping


def to_ref(path: Path, package_roots: dict[str, str]) -> str:
    """Render package-contained paths as portable ``@pkg/...`` references.

    Paths outside installed packages remain filesystem paths.
    """
    resolved = str(path.resolve())
    for root, name in package_roots.items():
        if resolved.startswith(root):
            rel = resolved[len(root) :].lstrip("/").lstrip("\\").replace("\\", "/")
            return f"@{name}/{rel}"
    return str(path)


def _parse_creature_detail(config_dir: Path) -> CatalogEntry | None:
    """Parse a creature directory, falling back to basic YAML metadata.

    Full config loading provides normalized model and tool data. Raw YAML keeps a
    partially valid creature discoverable when deeper validation fails.
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
        # Discovery tolerates validation failures when basic manifest metadata is
        # still readable.
        _ = e  # Retain the broad failure for the fallback boundary.
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
            _ = e  # An unreadable config is not a discoverable entry.
            return None


def _parse_terrarium_detail(config_dir: Path) -> CatalogEntry | None:
    """Parse a terrarium directory into a catalog entry when readable."""
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
        _ = e  # An unreadable terrarium is not a discoverable entry.
        return None


def _parse_creature_minimal(config_dir: Path) -> dict:
    """Read minimal creature metadata without validating the full agent config.

    The directory name is a stable fallback when YAML is absent or malformed.
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
        _ = e  # Preserve discoverability through the directory-name fallback.
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
        _ = e  # Preserve discoverability through the directory-name fallback.
        return {"name": config_dir.name, "description": ""}


def scan_catalog() -> list[CatalogEntry]:
    """Return visible package and workspace creatures and terrariums.

    Resolved-path deduplication collapses editable links and overlapping local
    scans. Entries without a usable name are excluded.
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

    # Manifest declarations define package visibility, including editable installs.
    for pkg in list_packages():
        pkg_path = Path(pkg["path"])
        pkg_name = pkg["name"]
        for c in pkg.get("creatures", []):
            rel = manifest_entry_rel_path(c, "creatures")
            if rel:
                _add_creature(pkg_path / rel, source=pkg_name)
        for t in pkg.get("terrariums", []):
            rel = manifest_entry_rel_path(t, "terrariums")
            if rel:
                _add_terrarium(pkg_path / rel, source=pkg_name)

    # Conventional workspace directories supplement installed package entries.
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

    return [r for r in results if r.name]


def scan_creatures_in_dirs(base_dirs: list[Path]) -> list[dict]:
    """Discover creature configs from package manifests and extra directories.

    Package discovery occurs at call time so runtime installs become visible
    without restart. Paths inside packages are rendered as ``@pkg/...`` refs, and
    resolved-path deduplication handles overlapping roots. Results are cached
    briefly by directory tuple; mutation paths invalidate the cache for immediate
    consistency.
    """
    global _creatures_cache
    key = _cache_key(base_dirs)
    now = time.time()
    if _creatures_cache is not None:
        cached_results, cached_at, cached_key = _creatures_cache
        if cached_key == key and now - cached_at < _SCAN_CACHE_TTL:
            return cached_results

    results: list[dict] = []
    seen: set[str] = set()
    package_roots = _build_package_root_map()

    def _emit(config_dir: Path) -> None:
        # Resolved paths unify package declarations, symlinks, and overlapping
        # configured roots under one identity.
        try:
            resolved = str(config_dir.resolve())
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        config_file = config_dir / "config.yaml"
        if not config_file.exists():
            config_file = config_dir / "config.yml"
        if not config_file.exists():
            return
        minimal = _parse_creature_minimal(config_dir)
        results.append(
            {
                "name": minimal["name"],
                "path": to_ref(config_dir, package_roots),
                "description": minimal["description"],
            }
        )

    # Package manifests are the authoritative package discovery source shared
    # with the full catalog.
    for pkg in list_packages():
        pkg_path = Path(pkg["path"])
        for c in pkg.get("creatures", []):
            rel = manifest_entry_rel_path(c, "creatures")
            if not rel:
                continue
            _emit(pkg_path / rel)

    # Additional roots support workspace and environment-specific discovery;
    # each direct child is a candidate creature directory.
    for base_dir in base_dirs:
        if not base_dir.is_dir():
            continue
        for child in sorted(base_dir.iterdir()):
            if child.is_dir():
                _emit(child)

    _creatures_cache = (results, now, key)
    return results


def scan_terrariums_in_dirs(base_dirs: list[Path]) -> list[dict]:
    """Discover terrarium configs from package manifests and extra directories.

    This follows the same current-install visibility, path rendering,
    deduplication, and cache invalidation rules as creature discovery.
    """
    global _terrariums_cache
    key = _cache_key(base_dirs)
    now = time.time()
    if _terrariums_cache is not None:
        cached_results, cached_at, cached_key = _terrariums_cache
        if cached_key == key and now - cached_at < _SCAN_CACHE_TTL:
            return cached_results

    results: list[dict] = []
    seen: set[str] = set()
    package_roots = _build_package_root_map()

    def _emit(config_dir: Path) -> None:
        try:
            resolved = str(config_dir.resolve())
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        config_file = config_dir / "terrarium.yaml"
        if not config_file.exists():
            config_file = config_dir / "terrarium.yml"
        if not config_file.exists():
            return
        minimal = _parse_terrarium_minimal(config_dir)
        results.append(
            {
                "name": minimal["name"],
                "path": to_ref(config_dir, package_roots),
                "description": minimal["description"],
            }
        )

    # Package manifests are the authoritative package discovery source.
    for pkg in list_packages():
        pkg_path = Path(pkg["path"])
        for t in pkg.get("terrariums", []):
            rel = manifest_entry_rel_path(t, "terrariums")
            if not rel:
                continue
            _emit(pkg_path / rel)

    # Additional roots supplement package discovery with local terrariums.
    for base_dir in base_dirs:
        if not base_dir.is_dir():
            continue
        for child in sorted(base_dir.iterdir()):
            if child.is_dir():
                _emit(child)

    _terrariums_cache = (results, now, key)
    return results


def dedupe_dirs(dirs: list[str]) -> list[Path]:
    """Resolve directory paths and preserve the first occurrence of each."""
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        p = Path(d).resolve()
        key = str(p)
        if key not in seen:
            out.append(p)
            seen.add(key)
    return out
