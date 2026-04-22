"""Isolation contract — core must never import from api/studio.

Guards the hard rule in ``plans/kt-studio/README.md §1``: code
outside ``src/kohakuterrarium/api/studio/`` must never import
from it. The only permitted touch point is
``src/kohakuterrarium/api/app.py`` (T1), which includes the
studio router.

If this test fails, a studio dep leaked into the core framework
and the whole "optional embedded section" property of studio is
broken. Fix the offending import — do **not** add the file to
the allowlist without an explicit plan amendment.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = REPO_ROOT / "src" / "kohakuterrarium"
STUDIO_DIR = CORE_DIR / "api" / "studio"

_IMPORT_RE = re.compile(
    r"(?:^|\n)\s*(?:from\s+kohakuterrarium\.api\.studio|"
    r"import\s+kohakuterrarium\.api\.studio)"
)

# Only ``api/app.py`` is permitted to include the studio router.
ALLOWLIST = {
    CORE_DIR / "api" / "app.py",
}


def _scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [m.group(0).strip() for m in _IMPORT_RE.finditer(text)]


def test_core_does_not_import_studio():
    """Walk src/kohakuterrarium, grep for studio imports.

    Skips files inside ``api/studio/`` itself (they own their own
    imports) and the permitted T1 touch point.
    """
    offenders: list[tuple[str, list[str]]] = []
    for py in CORE_DIR.rglob("*.py"):
        # Skip the studio subtree itself
        try:
            py.relative_to(STUDIO_DIR)
            continue
        except ValueError:
            pass
        if py in ALLOWLIST:
            continue
        hits = _scan_file(py)
        if hits:
            offenders.append((str(py.relative_to(REPO_ROOT)), hits))

    if offenders:
        lines = ["Studio imports leaked into core framework:"]
        for path, hits in offenders:
            for h in hits:
                lines.append(f"  {path}: {h}")
        lines.append(
            "\nIf you genuinely need this import, amend "
            "plans/kt-studio/README.md §1 (isolation contract) first."
        )
        raise AssertionError("\n".join(lines))


def test_touch_point_T1_includes_router():
    """The permitted touch point must still include the studio router.

    If someone removes the include by accident, studio is silently
    off — catch that here.
    """
    app_py = (CORE_DIR / "api" / "app.py").read_text(encoding="utf-8")
    assert "build_studio_router" in app_py, (
        "api/app.py must import + include build_studio_router "
        "(touch point T1 in plans/kt-studio/README.md §1)"
    )
    assert (
        "include_router(build_studio_router())" in app_py
    ), "api/app.py must call app.include_router(build_studio_router())"


def test_studio_subtree_exists():
    """Basic shape check — the studio package is present."""
    assert STUDIO_DIR.is_dir(), f"studio package missing: {STUDIO_DIR}"
    assert (STUDIO_DIR / "__init__.py").is_file()
    assert (STUDIO_DIR / "app.py").is_file()
