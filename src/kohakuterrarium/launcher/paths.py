"""Path constants the launcher reads / writes.

Centralised so test fixtures can monkey-patch ``CONFIG_HOME`` and the
rest of the launcher consults the patched value. Every helper here is
pure — no side effects on import.

Layout (see ``plans/1.5.0-roadmap/06b-release-bundle-update/design.md`` §1)::

    ~/.kohakuterrarium/
    ├── app-settings.json
    └── runtime/
        ├── active                       pointer JSON
        ├── .update.lock
        ├── versions/
        │   ├── 1.5.0/
        │   ├── 1.5.1/
        │   └── 1.5.0.partial/           in-flight extraction
        └── manifest-cache/
            ├── stable.json
            ├── beta.json
            └── nightly.json

The briefcase shell additionally bundles one offline-fallback
tarball at ``<bundle-root>/bundled-release/`` for first-launch without
network. ``bundled_release_dir()`` probes the candidates and returns
the first that contains a ``.tar.*`` artifact.
"""

import os
import sys
from pathlib import Path


def config_home() -> Path:
    """Resolve the user's KohakuTerrarium config dir.

    Honours ``KT_CONFIG_DIR`` so the launcher + the framework agree
    on where settings + runtime state live.
    """
    env = os.environ.get("KT_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".kohakuterrarium"


def runtime_dir() -> Path:
    """Where the launcher keeps its managed versions + lockfile."""
    return config_home() / "runtime"


def versions_dir() -> Path:
    """Parent dir for every installed version's side-by-side tree."""
    return runtime_dir() / "versions"


def version_dir(name: str) -> Path:
    """Tree for a specific version (e.g. ``"1.5.1"`` or ``"1.5.2.partial"``)."""
    return versions_dir() / name


def active_pointer_path() -> Path:
    """Pointer file resolved by the bootloader on every launch."""
    return runtime_dir() / "active"


def manifest_cache_dir() -> Path:
    """Where last-fetched channel manifests + ETag metadata live."""
    return runtime_dir() / "manifest-cache"


def settings_path() -> Path:
    """Path to the ``app-settings.json`` file."""
    return config_home() / "app-settings.json"


def lock_path() -> Path:
    """Path to the update-flock file."""
    return runtime_dir() / ".update.lock"


# ── Legacy 06 layout (kept for migration detection only) ────────────


def legacy_venv_dir() -> Path:
    """Where 06 (pre-superseded) kept the managed venv. We probe for it
    on startup and wipe if found — the 06 venv is broken on the
    briefcase shell anyway (missing ``venv`` module)."""
    return runtime_dir() / "venv"


# ── Bundled-release probe ───────────────────────────────────────────


def _candidate_bundled_release_dirs() -> list[Path]:
    """Where the briefcase shell might have laid down the offline tarball.

    Candidate order (first-match-wins):

    1. Sibling of ``kohakuterrarium/`` in the briefcase ``app/`` layout.
    2. Sibling of ``sys.executable`` (briefcase windows layout).
    3. Parent of ``sys.executable``'s directory (briefcase macOS legacy).
    4. Repo root for dev installs that ran the build helper locally.
    """
    here = Path(__file__).resolve()
    exe = Path(sys.executable)
    return [
        here.parent.parent.parent / "bundled-release",
        exe.parent / "bundled-release",
        exe.parent.parent / "bundled-release",
        here.parents[3] / "bundled-release",
    ]


def _has_release_tarball(path: Path) -> bool:
    """A bundled-release dir is valid only when it carries at least one
    ``kohakuterrarium-*.tar.*`` artifact."""
    if not path.is_dir():
        return False
    return any(path.glob("kohakuterrarium-*.tar.*"))


def bundled_release_dir() -> Path | None:
    """Return the bundled-release directory inside the briefcase artifact,
    or ``None`` for dev installs without one."""
    for candidate in _candidate_bundled_release_dirs():
        if _has_release_tarball(candidate):
            return candidate
    return None


# ── Per-version entrypoint paths ────────────────────────────────────


def kt_script(version_root: Path) -> Path:
    """Path to the ``kt`` console-script shim inside a version tree."""
    if sys.platform == "win32":
        return version_root / "scripts" / "kt.exe"
    return version_root / "scripts" / "kt"


def python_for(version_root: Path) -> Path:
    """Path to the python interpreter to run the version with.

    The launcher exec'd via the briefcase-shell-provided python — that
    interpreter is what runs ``versions/<v>/scripts/kt`` after
    ``os.execv``. We surface it here so smoke-testing can spawn the
    SAME interpreter rather than the shell's, keeping ABI consistent.
    """
    return Path(sys.executable)


__all__ = [
    "config_home",
    "runtime_dir",
    "versions_dir",
    "version_dir",
    "active_pointer_path",
    "manifest_cache_dir",
    "settings_path",
    "lock_path",
    "legacy_venv_dir",
    "bundled_release_dir",
    "_candidate_bundled_release_dirs",
    "_has_release_tarball",
    "kt_script",
    "python_for",
]
