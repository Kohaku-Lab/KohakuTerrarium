"""Rewrite the ``version`` field in ``[project]`` and ``[tool.briefcase]``.

Used by the nightly workflow to stamp a per-build dev version
(``2.0.0.dev<ts>+<sha>``) into the source tree before ``python -m
build`` / ``briefcase create``. Both sections must agree — briefcase
reads its own ``[tool.briefcase] version`` field, not ``[project]``.

Usage::

    python scripts/set_pyproject_version.py 2.0.0.dev20260520123456+abc1234
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def patch(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    new_line = f'version = "{version}"'
    # Match section-anchored ``version =`` lines and rewrite them.  The
    # file has exactly two: one under ``[project]`` and one under
    # ``[tool.briefcase]``.  A bare regex on ``^version = `` rewrites
    # both in one pass.
    updated, count = re.subn(
        r'^version = "[^"]*"',
        new_line,
        text,
        flags=re.MULTILINE,
    )
    if count == 0:
        raise SystemExit("no ``version = ...`` line matched in pyproject.toml")
    PYPROJECT.write_text(updated, encoding="utf-8")
    print(f"[set_pyproject_version] patched {count} version line(s) → {version}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: set_pyproject_version.py <version>", file=sys.stderr)
        return 2
    patch(sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
