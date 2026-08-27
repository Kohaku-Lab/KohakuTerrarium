"""Pin the Briefcase desktop requirement to a locally built wheel.

``[tool.briefcase.app.kohakuterrarium.<platform>] requires`` lists
``KohakuTerrarium[desktop]``, which pip resolves from PyPI. Desktop
bundles therefore ship the *last published* framework rather than the
one being built, and the build breaks outright once the working tree
and the published release disagree on a pinned dependency: the
``pywebview 6.1 -> 6.2.1`` bump left pip with ``pywebview==6.2.1``
(launcher requires, read from the working tree) against
``pywebview==6.1`` (the published wheel's ``[desktop]`` extra).

Rewriting the requirement to an exact version pin makes pip take the
wheel CI just built, via the existing
``requirement_installer_args = ["--find-links", "wheels"]``.

Usage::

    python scripts/pin_briefcase_local_wheel.py --wheel-dir wheels
"""

import argparse
import re
import sys
from pathlib import Path

# Matches the bare requirement and an already-pinned one, so re-running is safe.
REQUIREMENT_RE = re.compile(r'"KohakuTerrarium\[desktop\](?:==[^"]*)?"')


def parse_args() -> argparse.Namespace:
    """Parse wheel-discovery and pyproject options."""
    p = argparse.ArgumentParser(description="Pin the desktop requirement to a wheel.")
    p.add_argument(
        "--wheel-dir",
        type=Path,
        default=Path("wheels"),
        help="Directory holding the locally built kohakuterrarium wheel.",
    )
    p.add_argument(
        "--version",
        default="",
        help="Pin this version instead of reading it off the wheel filename.",
    )
    p.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    return p.parse_args()


def wheel_version(wheel_dir: Path) -> str:
    """Return the single kohakuterrarium version present in ``wheel_dir``."""
    wheels = sorted(wheel_dir.glob("kohakuterrarium-*.whl"))
    if not wheels:
        raise SystemExit(f"no kohakuterrarium wheel found in {wheel_dir}")
    # Wheel filenames are ``<name>-<version>-<python>-<abi>-<platform>.whl``.
    versions = {w.name.split("-")[1] for w in wheels}
    if len(versions) != 1:
        raise SystemExit(
            f"multiple kohakuterrarium versions in {wheel_dir}: {versions}"
        )
    return next(iter(versions))


def pin(pyproject: Path, version: str) -> int:
    """Rewrite every desktop requirement to ``==version``; return the count."""
    text = pyproject.read_text(encoding="utf-8")
    updated, count = REQUIREMENT_RE.subn(f'"KohakuTerrarium[desktop]=={version}"', text)
    if count == 0:
        raise SystemExit("no KohakuTerrarium[desktop] requirement found in pyproject")
    pyproject.write_text(updated, encoding="utf-8")
    return count


def main() -> int:
    """Resolve the local wheel version and pin the desktop requirement to it."""
    args = parse_args()
    version = args.version or wheel_version(args.wheel_dir)
    count = pin(args.pyproject, version)
    print(
        f"[pin-briefcase] pinned {count} requirement(s) -> KohakuTerrarium=={version}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
