"""Marketplace resolver — fetch + cache + resolve ``@name`` specs.

Fetches the marketplace ``registry.yaml`` (default: TerrariumMarket),
parses each entry into a typed :class:`MarketplaceEntry`, and
resolves ``@name`` / ``@name@version`` / ``@source/name`` to the
underlying git URL.  Does NOT clone or install —
:mod:`kohakuterrarium.packages.install` owns that side; we hand the
URL over and step away.

Source list at ``<config>/marketplace-sources.json``; empty / missing
→ built-in default.  Cache at ``<config>/marketplace/cache.json``
(``KT_MARKETPLACE_CACHE_TTL`` overridable, default 3600s).  Test
isolation via ``KT_CONFIG_DIR``.
"""

import asyncio
import importlib.metadata
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from kohakuterrarium.packages.marketplace_types import (
    IncompatibleFrameworkError,
    InvalidSpecError,
    MarketplaceEntry,
    MarketplaceError,
    MarketplaceNotFoundError,
    MarketplaceSource,
    MarketplaceUnavailableError,
    MarketplaceVersion,
)
from kohakuterrarium.utils.config_dir import config_dir, config_subdir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Re-exports — consumers can import errors + dataclasses from here too.
__all__ = [
    *("DEFAULT_SOURCE_URL", "DEFAULT_SOURCE_ALIAS"),
    *("IncompatibleFrameworkError", "InvalidSpecError", "MarketplaceError"),
    *("MarketplaceNotFoundError", "MarketplaceUnavailableError"),
    *("MarketplaceEntry", "MarketplaceSource", "MarketplaceVersion"),
    *("add_source", "remove_source", "reset_sources", "list_sources"),
    *("fetch_marketplace", "fetch_marketplace_sync", "invalidate_cache"),
    *("search", "search_sync", "resolve", "resolve_sync"),
    *("parse_spec", "is_spec", "install_url"),
]

# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

DEFAULT_SOURCE_URL = (
    "https://raw.githubusercontent.com/Kohaku-Lab/TerrariumMarket/main/registry.yaml"
)
DEFAULT_SOURCE_ALIAS = "default"

# Cache TTL — overridable via KT_MARKETPLACE_CACHE_TTL (seconds).
_DEFAULT_TTL_SECONDS = 3600

_SOURCES_FILENAME = "marketplace-sources.json"
_CACHE_FILENAME = "cache.json"
_CACHE_SUBDIR = "marketplace"
_CACHE_VERSION = 1


# ──────────────────────────────────────────────────────────────────
# Internal state — in-memory cache of the merged registry
# ──────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class _CacheEntry:
    fetched_at: float
    etag: str
    data: dict[str, Any]


_memory_cache: dict[str, _CacheEntry] | None = None
_memory_cache_at: float = 0.0


# ──────────────────────────────────────────────────────────────────
# Source-list management
# ──────────────────────────────────────────────────────────────────


def _sources_path() -> "os.PathLike[str]":
    return config_dir() / _SOURCES_FILENAME


def _cache_path() -> "os.PathLike[str]":
    return config_subdir(_CACHE_SUBDIR) / _CACHE_FILENAME


def _cache_ttl() -> int:
    raw = os.environ.get("KT_MARKETPLACE_CACHE_TTL", "").strip()
    if not raw:
        return _DEFAULT_TTL_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "Invalid KT_MARKETPLACE_CACHE_TTL; falling back to default",
            value=raw,
        )
        return _DEFAULT_TTL_SECONDS


def _builtin_default_source() -> MarketplaceSource:
    return MarketplaceSource(alias=DEFAULT_SOURCE_ALIAS, url=DEFAULT_SOURCE_URL)


def list_sources() -> list[MarketplaceSource]:
    """Return configured marketplace sources, in lookup order.

    Empty / missing settings file falls back to the built-in default
    (TerrariumMarket).  The ``KT_MARKETPLACE_SOURCES`` env var takes
    precedence over the file when set — comma-separated URLs, each
    aliased ``env_0`` / ``env_1`` / …
    """
    env_override = os.environ.get("KT_MARKETPLACE_SOURCES", "").strip()
    if env_override:
        urls = [u.strip() for u in env_override.split(",") if u.strip()]
        return [MarketplaceSource(alias=f"env_{i}", url=u) for i, u in enumerate(urls)]

    path = _sources_path()
    if not path.exists():
        return [_builtin_default_source()]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed to read marketplace-sources.json; using built-in default",
            error=str(exc),
        )
        return [_builtin_default_source()]
    raw = data.get("sources", []) if isinstance(data, dict) else []
    out: list[MarketplaceSource] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        out.append(
            MarketplaceSource(
                alias=item.get("alias") or url,
                url=url,
                added=item.get("added", ""),
            )
        )
    return out or [_builtin_default_source()]


def _save_sources(sources: list[MarketplaceSource]) -> None:
    path = _sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sources": [s.to_dict() for s in sources]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def add_source(url: str, *, alias: str | None = None) -> MarketplaceSource:
    """Append a source.  Duplicate URL OR alias rejected.

    Alias defaults to the URL — uniqueness on both axes keeps
    ``remove_source(alias)`` unambiguous (otherwise two sources
    sharing an alias would both be removed in one call).
    """
    url = url.strip()
    if not url:
        raise ValueError("URL is required")
    # Materialise the default if no settings file exists yet so the
    # write preserves the default + the user's addition both.
    sources = (
        list_sources() if _sources_path().exists() else [_builtin_default_source()]
    )
    requested_alias = (alias or url).strip()
    for existing in sources:
        if existing.url == url:
            raise ValueError(f"Source already configured: {url}")
        if existing.alias == requested_alias:
            raise ValueError(
                f"Alias already in use by {existing.url!r}: {requested_alias!r}"
            )
    new = MarketplaceSource(
        alias=requested_alias,
        url=url,
        added=_iso_now(),
    )
    sources.append(new)
    _save_sources(sources)
    invalidate_cache()
    logger.info("Marketplace source added", url=url, alias=new.alias)
    return new


def remove_source(url_or_alias: str) -> bool:
    """Remove a source by URL or alias.  Returns True if anything was removed."""
    sources = list_sources()
    kept = [s for s in sources if s.url != url_or_alias and s.alias != url_or_alias]
    if len(kept) == len(sources):
        return False
    _save_sources(kept)
    invalidate_cache()
    logger.info("Marketplace source removed", target=url_or_alias)
    return True


def reset_sources() -> None:
    """Drop the persisted list — next read returns the built-in default."""
    path = _sources_path()
    if path.exists():
        path.unlink()
    invalidate_cache()
    logger.info("Marketplace sources reset to default")


# ──────────────────────────────────────────────────────────────────
# Fetch + parse
# ──────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_disk_cache() -> dict[str, _CacheEntry]:
    """Read the disk cache.  Empty dict on first run or corruption."""
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Marketplace cache corrupted; resetting", error=str(exc))
        return {}
    if not isinstance(raw, dict) or raw.get("version") != _CACHE_VERSION:
        return {}
    out: dict[str, _CacheEntry] = {}
    for url, payload in (raw.get("sources") or {}).items():
        if not isinstance(payload, dict):
            continue
        out[url] = _CacheEntry(
            fetched_at=float(payload.get("fetched_at") or 0.0),
            etag=str(payload.get("etag") or ""),
            data=payload.get("data") or {},
        )
    return out


def _save_disk_cache(cache: dict[str, _CacheEntry]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _CACHE_VERSION,
        "sources": {
            url: {
                "fetched_at": entry.fetched_at,
                "etag": entry.etag,
                "data": entry.data,
            }
            for url, entry in cache.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def invalidate_cache() -> None:
    """Force the next :func:`fetch_marketplace` to hit the network.

    Both the in-memory cache and the on-disk file are cleared.  Called
    automatically after source-list mutations + by ``kt marketplace
    refresh``.
    """
    global _memory_cache, _memory_cache_at
    _memory_cache = None
    _memory_cache_at = 0.0
    path = _cache_path()
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Failed to remove marketplace cache file", error=str(exc))


async def _fetch_one(
    client: httpx.AsyncClient, source: MarketplaceSource, etag: str
) -> tuple[str, dict[str, Any] | None]:
    """Fetch one source.  Returns ``(new_etag, parsed_or_None)``.

    Conditional GET via ``If-None-Match`` — on 304 we return the
    unchanged-marker (``None``) so the caller keeps the cached data.
    Network failures raise; the caller decides whether to fall back
    to cache.
    """
    headers: dict[str, str] = {"Accept": "text/yaml, text/plain, */*"}
    if etag:
        headers["If-None-Match"] = etag
    resp = await client.get(source.url, headers=headers, timeout=20)
    if resp.status_code == 304:
        return etag, None
    resp.raise_for_status()
    text = resp.text
    parsed = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        raise MarketplaceError(f"{source.url}: registry.yaml must be a YAML mapping")
    new_etag = resp.headers.get("ETag", "")
    return new_etag, parsed


def _parse_entries(
    raw: dict[str, Any], source: MarketplaceSource
) -> list[MarketplaceEntry]:
    """Project the raw YAML into typed entries.  Tolerant of unknown keys."""
    packages = raw.get("packages") or []
    if not isinstance(packages, list):
        return []
    out: list[MarketplaceEntry] = []
    for item in packages:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        versions_raw = item.get("versions") or []
        versions: list[MarketplaceVersion] = []
        for v in versions_raw if isinstance(versions_raw, list) else []:
            if not isinstance(v, dict):
                continue
            tag = v.get("tag")
            if not isinstance(tag, str) or not tag:
                continue
            versions.append(
                MarketplaceVersion(
                    tag=tag,
                    released=str(v.get("released", "")),
                    framework=str(v.get("framework", "")),
                    notes=str(v.get("notes", "")),
                    notes_url=str(v.get("notes_url", "")),
                    commit=str(v.get("commit", "")),
                    yanked=bool(v.get("yanked", False)),
                )
            )
        if not versions:
            continue
        tags_raw = item.get("tags") or []
        tags = tuple(t for t in tags_raw if isinstance(t, str))
        out.append(
            MarketplaceEntry(
                name=name,
                repo=str(item.get("repo", "")),
                description=str(item.get("description", "")),
                tags=tags,
                author=str(item.get("author", "")),
                license=str(item.get("license", "")),
                framework=str(item.get("framework", "")),
                versions=tuple(versions),
                homepage=str(item.get("homepage", "")),
                source_url=source.url,
                source_alias=source.alias,
            )
        )
    return out


async def fetch_marketplace(*, force: bool = False) -> list[MarketplaceEntry]:
    """Merge every configured source into a single entry list.

    Honors the cache (in-memory + on-disk) unless ``force=True`` is
    passed.  Network failures fall back to cached data with a warning
    log; cold-cache failure raises :class:`MarketplaceUnavailableError`.

    Returns ALL entries un-deduplicated — same-name entries from
    different sources are returned together so :func:`resolve` can
    apply explicit ``@source/name`` filtering against the raw set.
    Consumers wanting the user-facing first-source-wins view should
    call :func:`search` (which dedupes via :func:`_dedup_first_wins`)
    instead.
    """
    global _memory_cache, _memory_cache_at
    sources = list_sources()
    ttl = _cache_ttl()
    now = time.time()

    if (
        not force
        and _memory_cache is not None
        and (now - _memory_cache_at) < ttl
        and all(s.url in _memory_cache for s in sources)
    ):
        return _project(_memory_cache, sources)

    cache = _load_disk_cache()
    if not force:
        # Try cache-only path: every source covered + within TTL.
        if all(
            s.url in cache and (now - cache[s.url].fetched_at) < ttl for s in sources
        ):
            _memory_cache = cache
            _memory_cache_at = now
            return _project(cache, sources)

    # Network refresh — fetch each source, falling back to cache on
    # any individual failure.
    new_cache = dict(cache)
    any_success = False
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for source in sources:
            existing = cache.get(source.url)
            etag = existing.etag if existing else ""
            try:
                new_etag, parsed = await _fetch_one(client, source, etag)
            except (httpx.HTTPError, MarketplaceError) as exc:
                if existing is not None:
                    logger.warning(
                        "Marketplace fetch failed; using cached copy",
                        url=source.url,
                        error=str(exc),
                    )
                    any_success = True  # cached data is still usable
                else:
                    logger.error(
                        "Marketplace fetch failed and no cache available",
                        url=source.url,
                        error=str(exc),
                    )
                continue
            if parsed is None:
                # 304 Not Modified — bump fetched_at to extend TTL.
                if existing is not None:
                    new_cache[source.url] = _CacheEntry(
                        fetched_at=now, etag=existing.etag, data=existing.data
                    )
                    any_success = True
                continue
            new_cache[source.url] = _CacheEntry(
                fetched_at=now, etag=new_etag, data=parsed
            )
            any_success = True

    if not any_success and not cache:
        raise MarketplaceUnavailableError(
            "Could not reach any configured marketplace source and no cache is available"
        )

    _save_disk_cache(new_cache)
    _memory_cache = new_cache
    _memory_cache_at = now
    return _project(new_cache, sources)


def _project(
    cache: dict[str, _CacheEntry], sources: list[MarketplaceSource]
) -> list[MarketplaceEntry]:
    """Merge cache entries into a flat entry list, source order preserved.

    Does NOT deduplicate by name — same-name entries from different
    sources are ALL returned.  Consumers apply their own dedup:

    * :func:`search` (and the JSON API list view) wants the user-facing
      "first-source-wins" rule, so it dedups.
    * :func:`resolve` needs to filter by source_alias BEFORE deduping
      (otherwise ``@myfork/kt-biome`` could be lost if the default
      source's ``kt-biome`` was deduped away first).
    """
    entries: list[MarketplaceEntry] = []
    for source in sources:
        entry = cache.get(source.url)
        if entry is None:
            continue
        for parsed in _parse_entries(entry.data, source):
            entries.append(parsed)
    return entries


def _dedup_first_wins(
    entries: list[MarketplaceEntry],
) -> list[MarketplaceEntry]:
    """First-source-wins dedup with a structured shadowing log."""
    seen: dict[str, str] = {}  # name → winner alias (for log clarity)
    out: list[MarketplaceEntry] = []
    for entry in entries:
        winner = seen.get(entry.name)
        if winner is not None:
            logger.info(
                "Duplicate marketplace entry shadowed",
                pkg=entry.name,
                skipped=entry.source_alias,
                winner=winner,
            )
            continue
        seen[entry.name] = entry.source_alias
        out.append(entry)
    return out


# ──────────────────────────────────────────────────────────────────
# Search + resolve
# ──────────────────────────────────────────────────────────────────


async def search(
    query: str = "", *, tag: str | None = None, author: str | None = None
) -> list[MarketplaceEntry]:
    """Substring + tag + author filter over the merged registry.

    User-facing list view, so duplicates across sources are deduped
    first-source-wins (with a shadowing log for visibility).
    """
    entries = _dedup_first_wins(await fetch_marketplace())
    q = query.strip().lower()
    tag_norm = (tag or "").strip().lower()
    author_norm = (author or "").strip().lower()
    out: list[MarketplaceEntry] = []
    for entry in entries:
        if q and q not in entry.name.lower() and q not in entry.description.lower():
            continue
        if tag_norm and tag_norm not in {t.lower() for t in entry.tags}:
            continue
        if author_norm and author_norm != entry.author.lower():
            continue
        out.append(entry)
    return out


def parse_spec(spec: str) -> tuple[str | None, str, str | None]:
    """Parse an ``@source/name@version`` spec.

    Returns ``(source_alias_or_None, name, version_or_None)``.  Raises
    :class:`InvalidSpecError` if the spec is not an ``@``-form.
    """
    if not spec or not spec.startswith("@"):
        raise InvalidSpecError(f"Not a marketplace spec: {spec!r}")
    body = spec[1:]
    # Split off version (rightmost ``@``); leftmost segment may carry
    # an explicit source alias separated by ``/``.
    version: str | None = None
    if "@" in body:
        body, version = body.rsplit("@", 1)
        version = version.strip() or None
    source_alias: str | None = None
    if "/" in body:
        source_alias, body = body.split("/", 1)
        source_alias = source_alias.strip() or None
    name = body.strip()
    if not name:
        raise InvalidSpecError(f"Empty package name in spec: {spec!r}")
    return source_alias, name, version


def is_spec(value: str) -> bool:
    """Cheap predicate — True if ``value`` looks like a marketplace spec."""
    return isinstance(value, str) and value.startswith("@")


def _current_framework_version() -> Version | None:
    """Best-effort lookup of the running KohakuTerrarium version.

    Returns ``None`` when the package metadata isn't readable (e.g.
    a `pip install -e .` without an install-time dist-info, or a
    Briefcase bundle stripped of importlib.metadata records).  A
    ``None`` framework version means we can't enforce the constraint;
    callers must treat it as "skip the check + log a warning".
    """
    try:
        raw = importlib.metadata.version("kohakuterrarium")
    except importlib.metadata.PackageNotFoundError:
        return None
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _framework_compatible(constraint: str, current: Version | None) -> bool:
    """True if ``constraint`` is satisfied by the running framework.

    Empty constraint / unknown current framework / malformed
    constraint = permissive (logged for the malformed case).  Better
    to over-install than to silently reject a real package because
    its entry.yaml typed the spec wrong.

    ``prereleases=True`` is passed explicitly so dev builds of the
    framework itself (e.g. ``2.0.0.dev11``) match constraints that
    cover their major like ``>=1.4.0,<3.0.0``.  Note: PEP 440's
    ``<X`` operator still excludes pre-releases of ``X``
    specifically, so entry authors targeting the dev-toward-N.0.0
    line should write ``<(N+1).0.0`` to admit ``N.0.0.devM`` users.
    """
    if not constraint or current is None:
        return True
    try:
        spec = SpecifierSet(constraint)
    except InvalidSpecifier:
        logger.warning(
            "Malformed framework constraint; allowing install",
            constraint=constraint,
        )
        return True
    return spec.contains(current, prereleases=True)


async def resolve(spec: str) -> tuple[MarketplaceEntry, MarketplaceVersion]:
    """Resolve a spec to ``(entry, version)``.

    - ``@name`` → newest non-yanked version satisfying the running
      framework's compatibility constraint
    - ``@name@v1.2.0`` → exact version (yanked + incompatible allowed
      for reproducibility; incompatible pin logs a warning)
    - ``@source/name`` → name restricted to a specific source alias

    Crucially, source-alias filtering happens BEFORE dedup so an
    explicit ``@myfork/name`` resolves correctly even when the
    default source shadows ``name``.

    Raises:
        InvalidSpecError: malformed spec.
        MarketplaceNotFoundError: name not in any (or specified) source.
        IncompatibleFrameworkError: no version satisfies the constraint.
    """
    source_alias, name, version = parse_spec(spec)
    # NB: we use the raw (un-deduped) entries here so an explicit
    # ``@source/name`` finds the source's own copy even when
    # first-source-wins dedup would have shadowed it.
    entries = await fetch_marketplace()
    candidates = [
        e
        for e in entries
        if e.name == name and (source_alias is None or e.source_alias == source_alias)
    ]
    if not candidates:
        raise MarketplaceNotFoundError(
            f"No marketplace entry named {name!r}"
            + (f" in source {source_alias!r}" if source_alias else "")
        )
    # First match wins (source-priority order is preserved from _project).
    entry = candidates[0]
    current = _current_framework_version()

    if version is not None:
        for v in entry.versions:
            if v.tag == version:
                constraint = v.framework or entry.framework
                if not _framework_compatible(constraint, current):
                    logger.warning(
                        "Installing marketplace version with incompatible "
                        "framework constraint (explicit pin)",
                        pkg=name,
                        version=version,
                        constraint=constraint,
                        running=str(current) if current else "unknown",
                    )
                return entry, v
        raise MarketplaceNotFoundError(
            f"{name}: version {version!r} not listed; "
            f"available: {', '.join(v.tag for v in entry.versions)}"
        )

    # Un-pinned: walk newest-first, skip yanked + incompatible.
    incompatible: list[str] = []
    for v in entry.versions:
        if v.yanked:
            continue
        constraint = v.framework or entry.framework
        if not _framework_compatible(constraint, current):
            incompatible.append(f"{v.tag} ({constraint})")
            continue
        return entry, v

    # Nothing satisfied the running framework.  The error message
    # carries both the incompatible candidates AND the running
    # framework so the user knows exactly what to upgrade / downgrade.
    if incompatible:
        raise IncompatibleFrameworkError(
            f"{name}: no version compatible with framework "
            f"{current or 'unknown'} — listed: {', '.join(incompatible)}"
        )
    raise IncompatibleFrameworkError(f"{name}: every listed version is yanked")


def install_url(entry: MarketplaceEntry, version: MarketplaceVersion) -> str:
    """Return the git URL ``install_package`` should clone for this entry.

    Just the entry's ``repo`` field — the ref pin (commit or tag) is
    passed separately as ``install_package(..., ref=...)``.  The
    ``install_package_spec`` wrapper composes the pair: ``url``
    comes from this helper, ``ref`` comes from
    ``version.commit or version.tag`` (commit preferred when CI has
    resolved it).
    """
    return entry.repo


# ──────────────────────────────────────────────────────────────────
# Sync wrapper (CLI ergonomics — most CLI handlers are sync)
# ──────────────────────────────────────────────────────────────────


def resolve_sync(spec: str) -> tuple[MarketplaceEntry, MarketplaceVersion]:
    """Synchronous wrapper around :func:`resolve` for CLI handlers."""
    return asyncio.run(resolve(spec))


def fetch_marketplace_sync(*, force: bool = False) -> list[MarketplaceEntry]:
    return asyncio.run(fetch_marketplace(force=force))


def search_sync(
    query: str = "", *, tag: str | None = None, author: str | None = None
) -> list[MarketplaceEntry]:
    return asyncio.run(search(query, tag=tag, author=author))
