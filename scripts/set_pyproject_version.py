"""Read, compute and stamp the version fields in ``pyproject.toml``.

``[project] version`` is the single source of truth. The nightly workflow asks
this script for the version rather than composing one inline, which is how a
build ends up claiming a version unrelated to the tree it was cut from.

Usage::

    python scripts/set_pyproject_version.py read
    python scripts/set_pyproject_version.py nightly     # X.Y.(Z+1).devYYYYmmdd
    python scripts/set_pyproject_version.py write 2.1.2.dev20260905030000
    python scripts/set_pyproject_version.py 2.1.2.dev20260905030000   # legacy form
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

VERSION_LINE = re.compile(r'^version = "([^"]*)"', re.MULTILINE)
CORE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def read() -> str:
    """Return the current ``[project] version`` string."""
    match = VERSION_LINE.search(PYPROJECT.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("no ``version = ...`` line matched in pyproject.toml")
    return match.group(1)


def nightly(version: str) -> str:
    """Return a PEP 440 dev release of the NEXT patch: ``X.Y.(Z+1).devYYYYmmdd``.

    Sorts above the released ``X.Y.Z`` and below the ``X.Y.Z+1`` it
    anticipates, so ``pip install --pre`` upgrades onto a nightly and the
    eventual patch release reads as the summary of those nightlies.

    The stamp is the UTC date, matching the ``nightly-YYYYmmdd`` release tag.
    Nightlies are cut once a day, so a finer stamp only lengthens the string.
    No ``+local`` segment: PyPI rejects local versions outright.
    """
    core = CORE.match(version)
    if not core:
        raise SystemExit(f"cannot parse version {version!r}")
    major, minor, patch_num = (int(g) for g in core.groups())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{major}.{minor}.{patch_num + 1}.dev{stamp}"


def patch(version: str) -> None:
    """Rewrite every authoritative top-level version field in pyproject.toml."""
    text = PYPROJECT.read_text(encoding="utf-8")
    # Project packaging and Briefcase each read their own top-level version field.
    updated, count = VERSION_LINE.subn(f'version = "{version}"', text)
    if count == 0:
        raise SystemExit("no ``version = ...`` line matched in pyproject.toml")
    PYPROJECT.write_text(updated, encoding="utf-8")
    print(f"[set_pyproject_version] patched {count} version line(s) -> {version}")


def main() -> int:
    """Dispatch read / nightly / write, accepting the legacy bare-version form."""
    args = sys.argv[1:]
    if not args:
        print(
            "usage: set_pyproject_version.py {read|nightly|write <version>|<version>}",
            file=sys.stderr,
        )
        return 2

    action = args[0]
    if action == "read":
        print(read())
    elif action == "nightly":
        print(nightly(read()))
    elif action == "write":
        if len(args) != 2:
            print("usage: set_pyproject_version.py write <version>", file=sys.stderr)
            return 2
        patch(args[1])
    elif len(args) == 1:
        patch(action)
    else:
        print(f"unknown action {action!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
