"""Build a release tarball for one (platform, py_abi).

Output: ``dist/kohakuterrarium-<version>-<plat>-py<X.Y>.tar.zst``
(or ``.tar.gz`` when ``--no-zstd``).

The tarball contains:

::

    kohakuterrarium-<version>-<plat>-py<X.Y>/
    ├── site-packages/   ← `pip install kohakuterrarium --target` output
    └── manifest.json    ← {version, build_id, platform, py_abi, sha256, ...}

That's it. No ``scripts/`` directory, no kt shim — the launcher uses
the briefcase-bundled Python directly (``sys.executable``) with
``PYTHONPATH`` pointing at ``site-packages/`` and dispatches into the
framework via ``-m kohakuterrarium.cli``. Shipping shim files would
just add per-platform fragility (PATH lookup, execv-cmd quirks on
Windows) for no benefit on top of what the launcher already has.

Usage::

    python scripts/build_release_tree.py \\
        --version 1.5.1 \\
        --platform linux-x64 \\
        --py-abi cp313 \\
        --channel stable \\
        --out dist/

CI runs this once per matrix cell. Locally you can run it without
``--platform`` / ``--py-abi`` — the script defaults to the current
machine's tags (useful for smoke).
"""

import argparse
import hashlib
import json
import platform as _platform
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    major, minor = sys.version_info[:2]
    return f"{impl}{major}{minor}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a release tarball.")
    p.add_argument("--version", required=True, help="PEP 440 version string.")
    p.add_argument(
        "--platform",
        default=current_platform_tag(),
        help=f"Platform tag (default: {current_platform_tag()}).",
    )
    p.add_argument(
        "--py-abi",
        default=current_py_abi_tag(),
        help=f"Python ABI tag (default: {current_py_abi_tag()}).",
    )
    p.add_argument(
        "--channel",
        default="stable",
        choices=("stable", "beta", "nightly"),
    )
    p.add_argument(
        "--extras",
        default="",
        help=(
            "pip extras to install (comma-separated, '' for none — the "
            "default). The bundled release intentionally omits heavy ML "
            "extras (``[full]`` pulls in sentence-transformers → torch → "
            "nvidia-* on Linux, ~2.5 GB per artifact). Users who want "
            "those install them on demand into the active version tree."
        ),
    )
    p.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "dist",
        help="Output directory for the tarball.",
    )
    p.add_argument(
        "--build-id",
        default=None,
        help="Override the build_id (default: <UTC-timestamp>-<git-sha-short>).",
    )
    p.add_argument(
        "--no-zstd",
        action="store_true",
        help="Emit .tar.gz instead of .tar.zst (broader compatibility).",
    )
    p.add_argument(
        "--source",
        default=str(REPO_ROOT),
        help="What to pass to `pip install` (default: the repo root).",
    )
    return p.parse_args()


def git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def default_build_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{git_short_sha()}"


def _pip_install_to(target: Path, source: str, extras: str) -> None:
    spec = source
    if extras:
        spec = f"{source}[{extras}]"
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-warn-script-location",
        "--target",
        str(target),
        spec,
    ]
    print(f"[release-tree] $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)


def _write_manifest(root: Path, info: dict) -> None:
    (root / "manifest.json").write_text(
        json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _pack_tarball(root: Path, tarball: Path, *, use_zstd: bool) -> None:
    """Pack ``root``'s **contents** (not ``root`` itself) into a tarball.

    The launcher extracts straight into ``runtime/versions/<v>/`` and
    expects ``site-packages/`` + ``manifest.json`` at the top level —
    no wrapping directory. Using ``tar.add(str(root), arcname=root.name)``
    would add an extra ``kohakuterrarium-<v>-<plat>-py<X.Y>/`` layer
    that ``smoke_test_tree`` then can't find.
    """
    tarball.parent.mkdir(parents=True, exist_ok=True)
    children = sorted(root.iterdir())
    if use_zstd:
        import zstandard  # only needed when packing zstd

        with tarball.open("wb") as dst:
            cctx = zstandard.ZstdCompressor(level=19, threads=-1)
            with cctx.stream_writer(dst) as compressor:
                with tarfile.open(fileobj=compressor, mode="w|") as tar:
                    for child in children:
                        tar.add(str(child), arcname=child.name)
    else:
        with tarfile.open(str(tarball), mode="w:gz", compresslevel=9) as tar:
            for child in children:
                tar.add(str(child), arcname=child.name)


def main() -> int:
    args = parse_args()
    build_id = args.build_id or default_build_id()

    work = REPO_ROOT / "build" / "release-tree"
    if work.exists():
        shutil.rmtree(work)
    root_name = (
        f"kohakuterrarium-{args.version}-{args.platform}-py{_py_minor(args.py_abi)}"
    )
    root = work / root_name
    site = root / "site-packages"
    site.mkdir(parents=True, exist_ok=True)

    _pip_install_to(site, args.source, args.extras)

    info = {
        "schema": 1,
        "name": "kohakuterrarium",
        "version": args.version,
        "build_id": build_id,
        "channel": args.channel,
        "platform": args.platform,
        "py_abi": args.py_abi,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_manifest(root, info)

    ext = ".tar.gz" if args.no_zstd else ".tar.zst"
    tarball = args.out / f"{root_name}{ext}"
    _pack_tarball(root, tarball, use_zstd=not args.no_zstd)
    info["sha256"] = _sha256(tarball)
    info["size_bytes"] = tarball.stat().st_size
    # Re-write the manifest (now with sha256) alongside the tarball for
    # the publish step to slurp.
    sidecar = tarball.with_suffix(tarball.suffix + ".manifest.json")
    sidecar.write_text(
        json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"[release-tree] wrote {tarball} ({info['size_bytes']} bytes)")
    print(f"[release-tree] wrote {sidecar}")
    print(f"[release-tree] sha256={info['sha256']}")
    return 0


def _py_minor(abi: str) -> str:
    """``cp313`` → ``3.13``."""
    digits = "".join(ch for ch in abi if ch.isdigit())
    if len(digits) >= 2:
        return f"{digits[0]}.{digits[1:]}"
    return abi


if __name__ == "__main__":
    sys.exit(main())
