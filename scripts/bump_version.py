"""Stamp a release version across every file that carries one.

``set_pyproject_version.py`` stamps the two ``pyproject.toml`` version
fields for an ephemeral nightly build. A formal release additionally
has to move ``__version__``, the Android ``version_code`` (asserted by
``tests/unit/packaging/test_android_release_config.py``) and the
frontend package metadata, which is what this script does.

Run from the repo root::

    python scripts/bump_version.py 2.1.1
"""

import argparse
import json
import re
import sys
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
INIT_PY = Path("src/kohakuterrarium/__init__.py")
PACKAGE_JSON = Path("src/kohakuterrarium-frontend/package.json")
PACKAGE_LOCK = Path("src/kohakuterrarium-frontend/package-lock.json")

RELEASE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_args() -> argparse.Namespace:
    """Parse the target release version."""
    p = argparse.ArgumentParser(description="Stamp a release version everywhere.")
    p.add_argument("version", help="Release version, e.g. 2.1.1.")
    p.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Checkout to rewrite (default: the working directory).",
    )
    return p.parse_args()


def android_version_code(version: str) -> str:
    """Encode ``X.Y.Z`` as the ``<major><minor:02><patch:02><build:03>`` code."""
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}{minor:02}{patch:02}000"


def _patch_pyproject(path: Path, version: str) -> None:
    """Rewrite both top-level version fields and the Android version code."""
    text = path.read_text(encoding="utf-8")
    text, count = re.subn(
        r'^version = "[^"]*"', f'version = "{version}"', text, flags=re.MULTILINE
    )
    if count != 2:
        raise SystemExit(f"expected 2 version lines in {path}, patched {count}")
    text, code_count = re.subn(
        r'^version_code = "[^"]*"',
        f'version_code = "{android_version_code(version)}"',
        text,
        flags=re.MULTILINE,
    )
    if code_count != 1:
        raise SystemExit(
            f"expected 1 version_code line in {path}, patched {code_count}"
        )
    path.write_text(text, encoding="utf-8")


def _patch_init(path: Path, version: str) -> None:
    """Rewrite the package's ``__version__`` assignment."""
    text = path.read_text(encoding="utf-8")
    text, count = re.subn(
        r'^__version__ = "[^"]*"',
        f'__version__ = "{version}"',
        text,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise SystemExit(f"expected 1 __version__ line in {path}, patched {count}")
    path.write_text(text, encoding="utf-8")


def _patch_json(path: Path, version: str, *, lockfile: bool) -> None:
    """Rewrite npm metadata versions, preserving npm's 2-space formatting."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    if lockfile:
        root_package = (data.get("packages") or {}).get("")
        if root_package is None:
            raise SystemExit(f"{path} has no root package entry to version")
        root_package["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def bump(repo_root: Path, version: str) -> list[Path]:
    """Stamp ``version`` across the checkout and return the files touched."""
    if not RELEASE_VERSION_RE.match(version):
        raise SystemExit(f"not a release version: {version!r} (expected X.Y.Z)")
    _patch_pyproject(repo_root / PYPROJECT, version)
    _patch_init(repo_root / INIT_PY, version)
    _patch_json(repo_root / PACKAGE_JSON, version, lockfile=False)
    _patch_json(repo_root / PACKAGE_LOCK, version, lockfile=True)
    return [
        repo_root / PYPROJECT,
        repo_root / INIT_PY,
        repo_root / PACKAGE_JSON,
        repo_root / PACKAGE_LOCK,
    ]


def main() -> int:
    """Stamp the requested version and report the rewritten files."""
    args = parse_args()
    for path in bump(args.repo_root, args.version):
        print(f"[bump-version] {path} -> {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
