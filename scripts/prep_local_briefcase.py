"""Populate ``bundled-release/`` so local ``briefcase create`` succeeds.

Briefcase's ``[tool.briefcase.app.kohakuterrarium] sources`` lists
``bundled-release`` as a required source directory. CI fills it with
the matrix output of ``build_release_tree.py``; locally, this helper
does the same against the current checkout + interpreter so a
developer can run ``briefcase create && briefcase build && briefcase
run`` and actually exercise the same launcher → first-install → exec
path real users hit.

Usage::

    python scripts/prep_local_briefcase.py
    briefcase create
    briefcase build
    briefcase run

The produced tarball is named with the local Python's platform / ABI
tags so the launcher's ``feeds.current_*_tag()`` match at runtime.
"""

import argparse
import platform as _platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_DIR = REPO_ROOT / "bundled-release"


def current_platform_tag() -> str:
    sysname = sys.platform
    machine = _platform.machine().lower()
    is_arm = machine in ("arm64", "aarch64")
    if sysname.startswith("linux"):
        return "linux-arm64" if is_arm else "linux-x64"
    if sysname == "darwin":
        return "macos-arm64" if is_arm else "macos-x64"
    if sysname == "win32":
        return "win-x64"
    return f"{sysname}-{machine}"


def current_py_abi_tag() -> str:
    impl = "cp" if sys.implementation.name == "cpython" else sys.implementation.name
    return f"{impl}{sys.version_info[0]}{sys.version_info[1]}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build a release-tree tarball for the local checkout + "
            "drop it into bundled-release/ so `briefcase create` "
            "succeeds locally."
        )
    )
    p.add_argument(
        "--version",
        default=None,
        help=(
            "PEP 440 version override. Defaults to '<pyproject-version>+local' "
            "so the build_id never collides with a real release."
        ),
    )
    p.add_argument(
        "--extras",
        default="",
        help=(
            "pip extras to install (comma-separated, '' for none). The "
            "real distribution ships with no extras; only override when "
            "you specifically need browser / discord / embeddings-heavy "
            "for a smoke run."
        ),
    )
    p.add_argument(
        "--no-zstd",
        action="store_true",
        help="Emit .tar.gz instead of .tar.zst (no zstandard dep).",
    )
    p.add_argument(
        "--keep-existing",
        action="store_true",
        help="Skip if bundled-release/ already has a kohakuterrarium-*.tar.* file.",
    )
    return p.parse_args()


def _read_local_version() -> str:
    pyproject = REPO_ROOT / "pyproject.toml"
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("version = "):
            return s.split("=", 1)[1].strip().strip('"')
    return "0.0.0"


def _existing_artifact() -> Path | None:
    if not BUNDLED_DIR.is_dir():
        return None
    for f in BUNDLED_DIR.iterdir():
        if f.name.startswith("kohakuterrarium-") and ".tar." in f.name:
            return f
    return None


def main() -> int:
    args = parse_args()

    if args.keep_existing:
        existing = _existing_artifact()
        if existing is not None:
            print(
                f"[prep] bundled-release/ already has {existing.name}; skipping build"
            )
            return 0

    version = args.version or f"{_read_local_version()}+local"
    plat = current_platform_tag()
    abi = current_py_abi_tag()
    out_dir = REPO_ROOT / "build" / "local-release-tree"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "build_release_tree.py"),
        "--version",
        version,
        "--platform",
        plat,
        "--py-abi",
        abi,
        "--channel",
        "stable",
        "--out",
        str(out_dir),
        "--build-id",
        f"local-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "--extras",
        args.extras,
    ]
    if args.no_zstd:
        cmd.append("--no-zstd")
    print(f"[prep] $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)

    # Move every produced tarball + sidecar into bundled-release/.
    BUNDLED_DIR.mkdir(parents=True, exist_ok=True)
    # Clean stale tarballs so the launcher's probe doesn't grab an
    # older artifact when multiple coexist.
    for stale in BUNDLED_DIR.glob("kohakuterrarium-*.tar.*"):
        stale.unlink()
    for stale in BUNDLED_DIR.glob("kohakuterrarium-*.manifest.json"):
        stale.unlink()
    moved = 0
    for produced in out_dir.iterdir():
        if produced.is_file() and (
            ".tar." in produced.name or produced.name.endswith(".manifest.json")
        ):
            shutil.copy2(produced, BUNDLED_DIR / produced.name)
            moved += 1
    print(
        f"[prep] copied {moved} artifact(s) into {BUNDLED_DIR.relative_to(REPO_ROOT)}/"
    )
    print("[prep] next: `briefcase create && briefcase build && briefcase run`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
