"""Verify a built Briefcase artifact actually carries wheels-bundle/.

Topic 06's bundled-first-install behaviour (sub-plan 01) requires that
``wheels-bundle/kohakuterrarium-*.whl`` exists inside the produced
artifact.  Without it, the launcher's ``bundled_wheels_dir()`` returns
``None`` and first-launch falls through to PyPI — exactly the gap
1.5.0's first wrapper release shipped with.

This script is run by the release workflow after every
``briefcase build`` so a missing wheels-bundle fails the job before
anything publishes.

Usage:
    python scripts/verify_bundled_wheels.py <artifact-path-or-build-dir>

The argument can be either:

- A built ``.app`` / ``.msi`` / ``.AppImage`` path, or
- A Briefcase build directory (e.g. ``build/kohakuterrarium/<platform>/``),
  in which case the search walks the directory tree.

The script exits 0 if it finds a ``wheels-bundle/`` directory
containing a ``kohakuterrarium-*.whl`` wheel.  It exits non-zero (with
a diagnostic) otherwise.
"""

import argparse
import sys
from pathlib import Path

WHEEL_GLOB = "kohakuterrarium-*.whl"


def find_wheels_bundles(root: Path) -> list[Path]:
    """Walk ``root`` and return every ``wheels-bundle/`` directory.

    A "match" is a directory NAMED ``wheels-bundle`` that contains at
    least one ``kohakuterrarium-*.whl`` file directly inside.
    """
    matches: list[Path] = []
    if not root.exists():
        return matches
    for path in root.rglob("wheels-bundle"):
        if not path.is_dir():
            continue
        wheels = list(path.glob(WHEEL_GLOB))
        if wheels:
            matches.append(path)
    return matches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifact",
        type=Path,
        help="Path to a built artifact (.app / .msi / .AppImage) or "
        "a Briefcase build directory.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only emit errors; suppress success diagnostics.",
    )
    args = parser.parse_args(argv)

    root: Path = args.artifact
    if not root.exists():
        print(f"ERROR: artifact not found: {root}", file=sys.stderr)
        return 2

    matches = find_wheels_bundles(root)
    if not matches:
        print(
            "ERROR: no wheels-bundle/ with a kohakuterrarium-*.whl found "
            f"under {root}.\n"
            "  Did `scripts/build_wrapper_wheels.py` run before "
            "`briefcase create`?\n"
            "  Does pyproject.toml's [tool.briefcase.app.kohakuterrarium] "
            "`sources` include 'wheels-bundle'?",
            file=sys.stderr,
        )
        return 1

    if not args.quiet:
        print(f"OK: found {len(matches)} wheels-bundle/ location(s) under {root}:")
        for m in matches:
            wheels = sorted(m.glob(WHEEL_GLOB))
            print(f"  {m}")
            for w in wheels:
                print(f"    - {w.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
