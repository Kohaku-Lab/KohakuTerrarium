"""Mobile-profile sandbox runtime helpers.

When the framework runs inside the Android APK (``KT_PROFILE=mobile``),
shell tools and any other ``subprocess.Popen`` callers can't rely on
``/bin/sh`` or PATH lookups — there is no /bin on Android.  Instead
the APK ships a bundled sandbox of static Linux binaries (busybox,
ripgrep, fd, git) extracted to the app's private files dir at
first launch.

This module resolves the bundled paths and exposes:

- :func:`is_mobile_profile` — cheap env-var check
- :func:`sandbox_bin_dir` — the dir holding bundled binaries
- :func:`sandbox_binary(name)` — resolve a specific bundled tool
  by canonical short name (``sh`` → busybox, ``rg`` → ripgrep, …)
- :func:`bundled_sh_command` — ``[busybox, "sh", "-c"]`` ready to
  splat into ``Popen``
- :func:`ensure_extracted` — first-launch extraction from APK
  assets into the writable bin dir (no-op when the bin dir already
  contains the manifest's binaries)

The runtime never imports anything Android-specific; the module
is pure Python and gracefully degrades on non-mobile platforms
(``is_mobile_profile()`` returns ``False``, helpers return ``None``).
"""

import json
import os
import stat
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Canonical short-name → bundled binary basename mapping.  The
# fetcher script (``packaging/android/fetch_sandbox.py``) lays down
# binaries under these exact names so runtime resolution doesn't
# need to know upstream filenames.
#
# Only ``busybox`` ships — it provides ``sh``, ``grep``, ``find``,
# ``sed``, ``awk``, ``curl``, and ~290 other commands as a single
# multicall binary.  The framework's standalone ``grep`` / ``glob``
# tools are pure-Python (no subprocess), so we don't need
# ripgrep / fd as separate binaries.  ``kt install`` works via
# pip without git on mobile; git-URL installs are documented as
# a 1.5.1 add via pygit2.
_CANONICAL_NAMES: dict[str, str] = {
    "sh": "busybox",
    "bash": "busybox",  # busybox sh is bash-compatible enough for the bash tool
    "busybox": "busybox",
    "curl": "busybox",  # busybox includes wget/curl applet
    "grep": "busybox",
    "find": "busybox",
    "sed": "busybox",
    "awk": "busybox",
}


def is_mobile_profile() -> bool:
    """``True`` iff the host is running under ``KT_PROFILE=mobile``.

    The launcher (Briefcase Android JNI entry) sets this env var
    before booting Python; we never set it ourselves at framework
    level.  Cheap to call on every tool invocation.
    """
    return os.environ.get("KT_PROFILE", "").strip().lower() == "mobile"


def sandbox_bin_dir() -> Path | None:
    """Return the directory holding bundled sandbox binaries.

    Resolution order:

    1. ``KT_SANDBOX_BIN_DIR`` env var (override — used by tests and
       the launcher when it knows the exact extracted path).
    2. ``<KT_CONFIG_DIR>/bin/`` when ``KT_CONFIG_DIR`` is set
       (Android launcher points config dir at the app's private
       files dir).
    3. ``~/.kohakuterrarium/bin/`` fallback for non-Android shells
       that opt in to ``KT_PROFILE=mobile`` for testing.

    Returns ``None`` if the dir doesn't exist — callers should treat
    that as "no sandbox available; fall back to platform PATH or
    fail with a clear message".
    """
    override = os.environ.get("KT_SANDBOX_BIN_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_dir() else None

    config_root = os.environ.get("KT_CONFIG_DIR", "").strip()
    if config_root:
        path = Path(config_root) / "bin"
        if path.is_dir():
            return path

    fallback = Path.home() / ".kohakuterrarium" / "bin"
    return fallback if fallback.is_dir() else None


def sandbox_binary(name: str) -> Path | None:
    """Resolve a bundled binary by canonical short name.

    Returns ``None`` when the bin dir doesn't exist or the binary
    isn't present.  Callers should distinguish between "no sandbox
    on this host" (mobile profile off) and "sandbox present but
    this tool missing" by checking :func:`is_mobile_profile` first.
    """
    canonical = _CANONICAL_NAMES.get(name)
    if canonical is None:
        return None
    bin_dir = sandbox_bin_dir()
    if bin_dir is None:
        return None
    candidate = bin_dir / canonical
    return candidate if candidate.is_file() else None


def bundled_sh_command(command: str) -> list[str] | None:
    """Return the argv to run ``command`` via the bundled
    ``busybox sh``.

    Returns ``None`` when there's no bundled busybox — caller falls
    back to a clear error.  The shape is
    ``[<bin>/busybox, "sh", "-c", <command>]`` which matches how
    busybox's multicall binary dispatches to its built-in ``sh``
    applet.
    """
    busybox = sandbox_binary("sh")
    if busybox is None:
        return None
    return [str(busybox), "sh", "-c", command]


def ensure_extracted(
    *,
    assets_root: Path | None = None,
    dest: Path | None = None,
    abi: str | None = None,
) -> Path | None:
    """First-launch extraction of bundled binaries from APK assets.

    On Android **the Java foreground service does the actual
    asset-extraction** (it has ``AssetManager`` + knows
    ``Build.SUPPORTED_ABIS``).  This Python-side fallback exists
    for:

    - Dev environments that sideload the bin dir
    - Recovery when Java extraction failed (rare; bad asset bundle)
    - Non-Android operators testing the mobile profile locally

    Supports two asset layouts:

    1. **Flat**: ``assets_root/manifest.json`` + ``assets_root/<binary>``
       — used by sideloaders that pick one ABI manually.
    2. **Per-ABI**: ``assets_root/manifest.json`` +
       ``assets_root/<abi>/<binary>`` — the layout the APK ships
       (matches ``packaging/android/bin/`` from ``fetch_sandbox.py``).

    The function probes both layouts and uses whichever has the
    binaries.  ``abi`` defaults to ``KT_SANDBOX_ABI`` env var, then
    every value in the manifest's ``abis`` list (the fetcher writes
    that).

    Idempotent: if every declared binary is already present and
    executable at ``dest``, this is a no-op.

    Returns the destination dir on success, ``None`` when no
    assets are available.  Logs INFO on extraction; WARNING on
    partial; ERROR on hard failure.
    """
    if assets_root is None:
        # The launcher sets ``KT_SANDBOX_ASSETS_DIR`` to the
        # extracted-from-APK source location.  When unset we have
        # nothing to extract and fall back to "user is responsible
        # for populating the bin dir".
        env_root = os.environ.get("KT_SANDBOX_ASSETS_DIR", "").strip()
        if not env_root:
            return None
        assets_root = Path(env_root)
    if not assets_root.is_dir():
        logger.debug(
            "mobile_sandbox.ensure_extracted: assets dir missing",
            assets_root=str(assets_root),
        )
        return None

    if dest is None:
        dest = sandbox_bin_dir()
        if dest is None:
            # Force-create under config dir.
            config_root = os.environ.get("KT_CONFIG_DIR", "").strip()
            if not config_root:
                logger.warning(
                    "mobile_sandbox.ensure_extracted: no KT_CONFIG_DIR "
                    "set; cannot decide where to extract binaries"
                )
                return None
            dest = Path(config_root) / "bin"

    manifest_path = assets_root / "manifest.json"
    if not manifest_path.is_file():
        logger.warning(
            "mobile_sandbox.ensure_extracted: manifest.json missing in assets",
            assets_root=str(assets_root),
        )
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(
            "mobile_sandbox.ensure_extracted: manifest unreadable",
            error=str(e),
        )
        return None

    binaries: list[str] = list(manifest.get("binaries") or [])
    if not binaries:
        logger.warning("mobile_sandbox.ensure_extracted: manifest declares no binaries")
        return None

    src_dir = _resolve_assets_layout(assets_root, binaries, manifest, abi)
    if src_dir is None:
        logger.warning(
            "mobile_sandbox.ensure_extracted: no usable binaries found in any "
            "layout (flat or per-ABI); manifest declares "
            f"{', '.join(binaries)}"
        )
        return None

    dest.mkdir(parents=True, exist_ok=True)
    # Idempotent skip — if every declared binary is already present
    # and executable, do nothing.
    if all(_is_executable(dest / name) for name in binaries):
        logger.debug("mobile_sandbox.ensure_extracted: already extracted, skipping")
        return dest

    extracted: list[str] = []
    for name in binaries:
        src = src_dir / name
        if not src.is_file():
            logger.warning(
                "mobile_sandbox.ensure_extracted: missing asset",
                binary=name,
                src_dir=str(src_dir),
            )
            continue
        target = dest / name
        # Copy bytes + chmod 0755 (rwxr-xr-x).  shutil.copy2 would
        # preserve src perms which are zip-archive-default (0644);
        # we want executable.
        target.write_bytes(src.read_bytes())
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        extracted.append(name)

    if not extracted:
        logger.error(
            "mobile_sandbox.ensure_extracted: nothing extracted; sandbox unavailable"
        )
        return None

    logger.info(
        "mobile_sandbox.ensure_extracted: extracted bundled binaries",
        count=len(extracted),
        dest=str(dest),
        src_layout=("per-abi" if src_dir != assets_root else "flat"),
    )
    return dest


def _resolve_assets_layout(
    assets_root: Path,
    binaries: list[str],
    manifest: dict,
    abi: str | None,
) -> Path | None:
    """Return the directory binaries actually live in, or None.

    Probes flat layout first (cheapest — one stat per binary), then
    per-ABI subdirs.  ABI resolution priority:

    1. Explicit ``abi`` arg
    2. ``KT_SANDBOX_ABI`` env var
    3. Each entry in ``manifest["abis"]`` (the fetcher writes this)
    """
    if all((assets_root / name).is_file() for name in binaries):
        return assets_root

    candidates: list[str] = []
    if abi:
        candidates.append(abi)
    env_abi = os.environ.get("KT_SANDBOX_ABI", "").strip()
    if env_abi and env_abi not in candidates:
        candidates.append(env_abi)
    for declared in manifest.get("abis", []) or []:
        if declared and declared not in candidates:
            candidates.append(declared)

    for candidate in candidates:
        sub = assets_root / candidate
        if sub.is_dir() and all((sub / name).is_file() for name in binaries):
            return sub
    return None


def _is_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.stat().st_mode & stat.S_IXUSR)
    except OSError:  # pragma: no cover - defensive
        return False


__all__ = [
    "is_mobile_profile",
    "sandbox_bin_dir",
    "sandbox_binary",
    "bundled_sh_command",
    "ensure_extracted",
]
