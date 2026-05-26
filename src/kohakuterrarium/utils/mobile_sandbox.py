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


# Canonical short-name → ordered candidate bundled-filename list.
# The bundled busybox provides ``sh``, ``grep``, ``find``, ``sed``,
# ``awk``, ``curl``, and ~290 other commands as a single multicall
# binary.  The framework's standalone ``grep`` / ``glob`` tools are
# pure-Python (no subprocess), so we don't need ripgrep / fd as
# separate binaries.
#
# Two acceptable bundled names:
#
# - ``libbusybox.so`` — when shipped as a native library (the
#   ``jniLibs/<abi>/libbusybox.so`` slot extracted by Android's
#   PackageManager into ``ApplicationInfo.nativeLibraryDir``).
#   This is the ONLY layout that survives Android 10+'s W^X /
#   noexec policy on app data dirs — executables anywhere else
#   under ``/data/data/<pkg>/`` are rejected by ``execve()``.
# - ``busybox`` — legacy / desktop-emulator name, kept for backward
#   compatibility with dev environments that sideload a binary
#   into ``KT_SANDBOX_BIN_DIR`` without the ``lib*.so`` prefix.
#
# Resolution probes in order: first match wins.  When the bundled
# name is ``libbusybox.so`` callers can detect that via
# :func:`sandbox_binary` returning a path whose name ends in
# ``.so`` and override argv[0] with the canonical applet name.
_CANONICAL_NAMES: dict[str, tuple[str, ...]] = {
    "sh": ("libbusybox.so", "busybox"),
    "bash": ("libbusybox.so", "busybox"),
    "busybox": ("libbusybox.so", "busybox"),
    "curl": ("libbusybox.so", "busybox"),
    "grep": ("libbusybox.so", "busybox"),
    "find": ("libbusybox.so", "busybox"),
    "sed": ("libbusybox.so", "busybox"),
    "awk": ("libbusybox.so", "busybox"),
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

    Probes the candidates from :data:`_CANONICAL_NAMES` in order
    (``libbusybox.so`` first — the Android native-library layout —
    then plain ``busybox``).  Returns ``None`` when no candidate
    file exists in the bin dir, or when the bin dir itself
    doesn't exist.  Callers should distinguish between "no sandbox
    on this host" (mobile profile off) and "sandbox present but
    this tool missing" by checking :func:`is_mobile_profile` first.
    """
    candidates = _CANONICAL_NAMES.get(name)
    if not candidates:
        return None
    bin_dir = sandbox_bin_dir()
    if bin_dir is None:
        return None
    for candidate in candidates:
        target = bin_dir / candidate
        if target.is_file():
            return target
    return None


def bundled_sh_command(command: str) -> list[str] | None:
    """Return the argv to run ``command`` via the bundled busybox.

    Returns ``None`` when there's no bundled busybox — caller falls
    back to a clear error.  The shape is
    ``["busybox", "sh", "-c", <command>]`` (argv[0] always reads as
    the canonical applet name ``busybox``); the actual executable
    path comes from :func:`bundled_sh_exe` so callers running on
    Android can pass that to :data:`subprocess.Popen` via the
    ``executable=`` kwarg — required when the bundled binary is
    named ``libbusybox.so`` (the native-library layout), since
    busybox's multicall dispatch can't recognise ``libbusybox.so``
    as its own applet name.
    """
    if bundled_sh_exe() is None:
        return None
    return ["busybox", "sh", "-c", command]


def bundled_sh_exe() -> Path | None:
    """Path to the bundled busybox executable.

    Companion to :func:`bundled_sh_command` — that returns the argv
    with a forced ``argv[0] = "busybox"``, this returns the actual
    file to ``execve()``.  Pass to ``subprocess.Popen(executable=…)``
    so the kernel runs the right binary while busybox sees the
    multicall name it expects.
    """
    return sandbox_binary("sh")


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


def default_workdir() -> Path:
    """Process-wide default working directory for newly-spawned agents.

    The historical default was ``Path.cwd()`` which works fine on
    desktop (the operator launched ``kt run`` from a directory and
    expects that directory to be the agent's workspace) but is
    broken on Android — Briefcase boots Python with ``cwd = /``,
    which the app has no permission to read or write.  Tools that
    resolve relative paths against cwd then fail with
    ``PermissionError`` on their first invocation.

    On the mobile profile this returns ``<KT_CONFIG_DIR>/work/``,
    which is the app's private files dir on Android (writable
    without permissions, visible to the user via the Files app
    under the KohakuTerrarium namespace).  The directory is created
    lazily on first call so callers don't have to.

    On any other platform — and as a fallback when the mobile
    profile is set but ``KT_CONFIG_DIR`` is not — this returns
    ``Path.cwd()`` so the desktop behaviour is unchanged.
    """
    if is_mobile_profile():
        config_root = os.environ.get("KT_CONFIG_DIR", "").strip()
        if config_root:
            workdir = Path(config_root) / "work"
            try:
                workdir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning(
                    "default_workdir mkdir failed; falling back to cwd",
                    path=str(workdir),
                    error=str(exc),
                )
                return Path.cwd()
            return workdir
    return Path.cwd()


__all__ = [
    "is_mobile_profile",
    "sandbox_bin_dir",
    "sandbox_binary",
    "bundled_sh_command",
    "bundled_sh_exe",
    "default_workdir",
    "ensure_extracted",
]
