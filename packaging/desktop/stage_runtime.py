"""Stage what the Tauri shell bundles: an interpreter, the launcher, a release.

The installed app ships three resource directories next to the executable:

* ``python/``          a relocatable CPython (python-build-standalone)
* ``bootstrap/``       the launcher package plus its two pure-Python dependencies,
                       enough to install and activate a versioned release tree
* ``bundled-release/`` the release-tree archive the launcher installs on first run

Usage::

    python packaging/desktop/stage_runtime.py --platform win-x64 \\
        --release-dir bundled-release/
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PACKAGE = REPO_ROOT / "src" / "kohakuterrarium"
STAGE = HERE / "stage"
RUNTIME_PIN = HERE / "python-runtime.json"
TAURI_CONF = HERE / "src-tauri" / "tauri.conf.json"

BOOTSTRAP_DEPENDENCIES = ("zstandard", "packaging")
LAUNCHER_EXCLUDED = frozenset({"android.py"})
RELEASE_GLOBS = ("kohakuterrarium-*.tar.*",)

Fetch = Callable[[str], bytes]


def core_version(version: str) -> str:
    """Return the numeric ``X.Y.Z`` an installer accepts for ``version``."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"cannot parse version {version!r}")
    return ".".join(match.groups())


def project_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]*)"', text, re.MULTILINE)
    if not match:
        raise ValueError("pyproject.toml has no version line")
    return match.group(1)


def stamp_version(conf_path: Path, version: str) -> str:
    """Write the installer version into the Tauri config and return it."""
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    conf["version"] = core_version(version)
    conf_path.write_text(json.dumps(conf, indent=2) + "\n", encoding="utf-8")
    return conf["version"]


def python_asset(pin: dict, platform: str) -> tuple[str, str]:
    """Return ``(archive name, download URL)`` of the pinned interpreter."""
    triple = pin["triples"].get(platform)
    if triple is None:
        raise ValueError(f"no pinned interpreter for platform {platform!r}")
    name = f"cpython-{pin['python']}+{pin['tag']}-{triple}-{pin['variant']}.tar.gz"
    return name, f"{pin['source']}/{pin['tag']}/{name.replace('+', '%2B')}"


def expected_sha256(sums: str, name: str) -> str:
    """Find ``name`` in a ``SHA256SUMS`` listing."""
    for line in sums.splitlines():
        digest, _, listed = line.strip().partition(" ")
        if listed.strip().lstrip("*") == name:
            return digest
    raise ValueError(f"{name} is not listed in SHA256SUMS")


def fetch_url(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310
        return response.read()


def download_python(
    pin: dict, platform: str, destination: Path, fetch: Fetch = fetch_url
) -> Path:
    """Download the pinned interpreter archive and verify its checksum."""
    name, url = python_asset(pin, platform)
    sums_url = f"{pin['source']}/{pin['tag']}/SHA256SUMS"
    wanted = expected_sha256(fetch(sums_url).decode("utf-8"), name)
    data = fetch(url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != wanted:
        raise ValueError(f"{name}: checksum {actual} does not match {wanted}")
    archive = destination / name
    archive.write_bytes(data)
    return archive


def interpreter_path(root: Path, platform: str) -> Path:
    """Where the staged interpreter executable sits under ``root``."""
    if platform.startswith("win"):
        return root / "python.exe"
    return root / "bin" / "python3"


PRUNED = ("test", "idlelib", "turtledemo")


def prune_python(root: Path) -> list[Path]:
    """Remove standard-library parts an installed app never runs; return what went."""
    removed = []
    for lib in (root / "Lib", *(root / "lib").glob("python3*")):
        for name in PRUNED:
            target = lib / name
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(target)
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    return removed


def extract_python(archive: Path, destination: Path) -> Path:
    """Unpack an ``install_only`` archive so the interpreter sits under ``destination``."""
    if destination.exists():
        shutil.rmtree(destination)
    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(scratch, filter="data")
        shutil.move(str(Path(scratch) / "python"), str(destination))
    prune_python(destination)
    return destination


def stage_bootstrap(
    package: Path,
    destination: Path,
    install: bool = True,
    python: Path | str = sys.executable,
) -> Path:
    """Copy the launcher package and install its dependencies beside it.

    Dependencies are installed with ``python`` so their ABI matches the interpreter
    the app ships.
    """
    if destination.exists():
        shutil.rmtree(destination)
    target = destination / "kohakuterrarium"
    target.mkdir(parents=True)
    shutil.copy2(package / "__init__.py", target / "__init__.py")
    shutil.copytree(
        package / "launcher",
        target / "launcher",
        ignore=shutil.ignore_patterns("__pycache__", *LAUNCHER_EXCLUDED),
    )
    if install:
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--only-binary=:all:",
                "--target",
                str(destination),
                *BOOTSTRAP_DEPENDENCIES,
            ],
            check=True,
        )
    return destination


def stage_release(release_dir: Path, destination: Path) -> list[Path]:
    """Copy the release-tree archive (and its manifest) the launcher installs."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    copied = []
    for pattern in RELEASE_GLOBS:
        for source in sorted(release_dir.glob(pattern)):
            copied.append(Path(shutil.copy2(source, destination / source.name)))
    if not copied:
        raise FileNotFoundError(f"no release archive in {release_dir}")
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--platform", required=True, choices=sorted(_triples()))
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--stage", type=Path, default=STAGE)
    parser.add_argument("--stamp-version", action="store_true")
    parser.add_argument("--skip-python", action="store_true")
    args = parser.parse_args(argv)

    pin = json.loads(RUNTIME_PIN.read_text(encoding="utf-8"))
    if not args.skip_python:
        with tempfile.TemporaryDirectory() as scratch:
            archive = download_python(pin, args.platform, Path(scratch))
            extract_python(archive, args.stage / "python")
    shipped = interpreter_path(args.stage / "python", args.platform)
    stage_bootstrap(
        PACKAGE,
        args.stage / "bootstrap",
        python=shipped if shipped.is_file() else sys.executable,
    )
    stage_release(args.release_dir, args.stage / "bundled-release")
    if args.stamp_version:
        print("installer version", stamp_version(TAURI_CONF, project_version()))
    return 0


def _triples() -> dict:
    return json.loads(RUNTIME_PIN.read_text(encoding="utf-8"))["triples"]


if __name__ == "__main__":
    sys.exit(main())
