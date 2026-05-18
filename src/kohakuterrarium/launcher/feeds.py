"""Feed resolution — pick the right release tarball for this machine.

Two feed kinds (see ``plans/1.5.0-roadmap/06b-release-bundle-update/design.md`` §4):

- ``github_releases`` — fetch
  ``https://github.com/<repo>/releases/download/manifests-<channel>/<channel>.json``.
  The release pipeline pre-publishes the channel manifest to a stable
  URL so the launcher doesn't have to walk the GitHub releases API
  (rate-limited, paginated).
- ``custom`` — fetch ``<feed.url>/<channel>.json`` verbatim. The
  manifest schema is the same; URLs inside the manifest point wherever
  the operator hosts the tarballs.

Resolution picks: (1) the channel manifest, (2) the release by version
(pinned → that one; else newest), (3) the artifact matching this
machine's ``(platform, py_abi)`` tag.

This module uses ``urllib`` only — no third-party HTTP client. Stale-
manifest cache lives at ``manifest-cache/<channel>.json`` with sibling
``.meta.json`` storing ETag / Last-Modified for conditional GETs.
"""

import json
import platform
import struct
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import manifest_cache_dir
from kohakuterrarium.launcher.settings import AppSettings

MANIFEST_SCHEMA = 1
USER_AGENT = "KohakuTerrarium-Launcher/1"


class FeedError(RuntimeError):
    """Raised when feed resolution fails (network, schema, no match)."""


@dataclass
class ReleaseTarget:
    version: str
    build_id: str
    url: str
    sha256: str
    size_bytes: int
    platform: str
    py_abi: str
    release_notes_url: str | None = None


# ── Platform / ABI tags ─────────────────────────────────────────────


def current_platform_tag() -> str:
    """``linux-x64`` / ``linux-arm64`` / ``macos-x64`` / ``macos-arm64`` / ``win-x64``."""
    sysname = sys.platform
    machine = platform.machine().lower()
    is_arm = machine in ("arm64", "aarch64")
    if sysname.startswith("linux"):
        return "linux-arm64" if is_arm else "linux-x64"
    if sysname == "darwin":
        return "macos-arm64" if is_arm else "macos-x64"
    if sysname == "win32":
        # 32-bit windows is unsupported by the framework matrix; we tag
        # everything ``win-x64`` and let smoke-test catch ABI mismatch
        # if someone tries it.
        return "win-x64"
    return f"{sysname}-{machine}"


def current_py_abi_tag() -> str:
    """``cp311`` / ``cp312`` / ``cp313`` / ``cp314`` — matches wheel tags."""
    impl = "cp" if sys.implementation.name == "cpython" else sys.implementation.name
    major, minor = sys.version_info[:2]
    # struct.calcsize("P") == 8 on 64-bit; we record but don't tag-suffix
    # since the framework matrix is 64-bit only.
    _ = struct.calcsize("P")
    return f"{impl}{major}{minor}"


# ── Manifest fetch ──────────────────────────────────────────────────


def _channel_manifest_url(settings: AppSettings) -> str:
    """Resolve the URL to GET for the channel manifest."""
    feed = settings.feed
    channel = settings.channel
    if feed.kind == "github_releases":
        repo = feed.repo
        return (
            f"https://github.com/{repo}/releases/download/"
            f"manifests-{channel}/{channel}.json"
        )
    if feed.kind == "custom":
        base = (feed.url or "").rstrip("/")
        if not base:
            raise FeedError("feed.kind=custom but feed.url is empty")
        return f"{base}/{channel}.json"
    raise FeedError(f"unknown feed.kind {feed.kind!r}")


def _read_etag_meta(channel: str) -> dict:
    meta = manifest_cache_dir() / f"{channel}.meta.json"
    if not meta.is_file():
        return {}
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_etag_meta(channel: str, etag: str | None, last_mod: str | None) -> None:
    out = manifest_cache_dir()
    out.mkdir(parents=True, exist_ok=True)
    meta = out / f"{channel}.meta.json"
    payload = {"etag": etag, "last_modified": last_mod}
    tmp = meta.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    tmp.replace(meta)


def _cached_manifest_path(channel: str) -> Path:
    return manifest_cache_dir() / f"{channel}.json"


def _read_cached_manifest(channel: str) -> dict | None:
    p = _cached_manifest_path(channel)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cached_manifest(channel: str, data: dict) -> None:
    out = manifest_cache_dir()
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{channel}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def fetch_manifest(settings: AppSettings, *, force_refresh: bool = False) -> dict:
    """Fetch the channel manifest, using conditional GET when possible.

    Returns the parsed JSON dict. Falls back to a stale cached copy if
    the network is unreachable. Raises :class:`FeedError` if no
    manifest can be obtained at all (no cache + network failure).
    """
    log = get_logger()
    url = _channel_manifest_url(settings)
    channel = settings.channel
    cached = None if force_refresh else _read_cached_manifest(channel)
    meta = _read_etag_meta(channel) if not force_refresh else {}

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if meta.get("etag"):
        req.add_header("If-None-Match", meta["etag"])
    if meta.get("last_modified"):
        req.add_header("If-Modified-Since", meta["last_modified"])

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.getcode()
            if status == 304 and cached is not None:
                return cached
            body = resp.read()
            etag = resp.headers.get("ETag")
            last_mod = resp.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached is not None:
            return cached
        if cached is not None:
            log.warning(
                "feeds: manifest fetch HTTP %d for %s; using stale cache", e.code, url
            )
            return cached
        raise FeedError(f"manifest fetch failed: HTTP {e.code} for {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if cached is not None:
            log.warning(
                "feeds: manifest fetch network error (%s); using stale cache", e
            )
            return cached
        raise FeedError(f"manifest fetch network error: {e}") from e

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise FeedError(f"manifest is not valid JSON: {e}") from e
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
        raise FeedError(
            f"manifest schema mismatch (expected {MANIFEST_SCHEMA}, got {data.get('schema')!r})"
        )
    if data.get("channel") != channel:
        log.warning(
            "feeds: manifest channel %r does not match requested %r",
            data.get("channel"),
            channel,
        )
    _write_cached_manifest(channel, data)
    _write_etag_meta(channel, etag, last_mod)
    return data


# ── Resolution ──────────────────────────────────────────────────────


def _pick_release(manifest: dict, pinned: str | None) -> dict:
    releases = manifest.get("releases") or []
    if not isinstance(releases, list) or not releases:
        raise FeedError("manifest has no releases")
    if pinned is None:
        return releases[0]
    for rel in releases:
        if rel.get("version") == pinned:
            return rel
    raise FeedError(f"pinned version {pinned!r} not present in channel manifest")


def _pick_artifact(release: dict, plat: str, abi: str) -> dict:
    artifacts = release.get("artifacts") or []
    for art in artifacts:
        if art.get("platform") == plat and art.get("py_abi") == abi:
            return art
    raise FeedError(
        f"no artifact for platform={plat!r} py_abi={abi!r} in version {release.get('version')!r}"
    )


def resolve_feed(
    settings: AppSettings,
    *,
    force_refresh: bool = False,
    platform_tag: str | None = None,
    py_abi_tag: str | None = None,
) -> ReleaseTarget:
    """End-to-end: fetch manifest, pick release, pick artifact for this machine."""
    plat = platform_tag or current_platform_tag()
    abi = py_abi_tag or current_py_abi_tag()
    manifest = fetch_manifest(settings, force_refresh=force_refresh)
    release = _pick_release(manifest, settings.pinned_version)
    artifact = _pick_artifact(release, plat, abi)
    return ReleaseTarget(
        version=release["version"],
        build_id=release.get("build_id") or "",
        url=artifact["url"],
        sha256=artifact["sha256"],
        size_bytes=int(artifact.get("size_bytes") or 0),
        platform=plat,
        py_abi=abi,
        release_notes_url=release.get("release_notes_url"),
    )


def list_available_releases(
    manifest: dict, *, platform_tag: str, py_abi_tag: str
) -> list[dict]:
    """For the UI: return the subset of manifest releases that have a
    build for ``(platform_tag, py_abi_tag)``, in newest-first order.

    Each entry is a public-friendly dict; the API layer can JSON-serialize.
    """
    out: list[dict] = []
    for rel in manifest.get("releases") or []:
        art = next(
            (
                a
                for a in (rel.get("artifacts") or [])
                if a.get("platform") == platform_tag and a.get("py_abi") == py_abi_tag
            ),
            None,
        )
        if art is None:
            continue
        out.append(
            {
                "version": rel.get("version"),
                "build_id": rel.get("build_id"),
                "release_notes_url": rel.get("release_notes_url"),
                "size_bytes": int(art.get("size_bytes") or 0),
            }
        )
    return out


__all__ = [
    "MANIFEST_SCHEMA",
    "FeedError",
    "ReleaseTarget",
    "current_platform_tag",
    "current_py_abi_tag",
    "fetch_manifest",
    "resolve_feed",
    "list_available_releases",
]
