"""Layer independence — enforce the cleanup-tier dependency rules.

Phase 0 of the studio-cleanup refactor introduces three new tiers:

- ``packages/`` — low-tier package storage / resolution. Cannot import
  from ``studio/`` or higher tiers (``api/``, ``cli/``).
- ``studio/`` — Studio orchestration over engine + low-tier libs. Not
  imported by ``core``/``bootstrap``/``compose``/``terrarium``/``packages``;
  does not import ``api/`` or ``cli/``.
- The legacy ``api/studio/`` prototype is *also* still treated as
  "embedded section" — only ``api/app.py`` may include its router (the
  T1 touch point preserved from the original test).

If this test fails, a tier dependency leaked and the Phase 0
foundation property is broken. Fix the offending import — do not add
the file to the allowlist without an explicit plan amendment.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "kohakuterrarium"
STUDIO_DIR = SRC_DIR / "studio"
PACKAGES_DIR = SRC_DIR / "packages"
LEGACY_API_STUDIO_DIR = SRC_DIR / "api" / "studio"


def _import_re(target_module: str) -> re.Pattern:
    return re.compile(
        rf"(?:^|\n)\s*(?:from\s+{re.escape(target_module)}|"
        rf"import\s+{re.escape(target_module)})"
    )


_STUDIO_RE = _import_re("kohakuterrarium.studio")
_API_RE = _import_re("kohakuterrarium.api")
_CLI_RE = _import_re("kohakuterrarium.cli")
_LEGACY_API_STUDIO_RE = re.compile(
    r"(?:^|\n)\s*(?:from\s+kohakuterrarium\.api\.studio|"
    r"import\s+kohakuterrarium\.api\.studio)"
)

# Only ``api/app.py`` is permitted to include the legacy studio router.
LEGACY_API_STUDIO_ALLOWLIST = {
    SRC_DIR / "api" / "app.py",
}


def _scan_file(path: Path, regex: re.Pattern) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [m.group(0).strip() for m in regex.finditer(text)]


def _walk(root: Path):
    for py in root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        yield py


def _is_under(path: Path, ancestor: Path) -> bool:
    try:
        path.relative_to(ancestor)
        return True
    except ValueError:
        return False


def test_lower_tiers_do_not_import_studio():
    """``core``/``bootstrap``/``compose``/``terrarium``/``packages`` must not import ``studio/``."""
    forbidden_roots = ["core", "bootstrap", "compose", "terrarium", "packages"]
    offenders: list[tuple[str, list[str]]] = []
    for root_name in forbidden_roots:
        root = SRC_DIR / root_name
        if not root.is_dir():
            continue
        for py in _walk(root):
            hits = _scan_file(py, _STUDIO_RE)
            if hits:
                offenders.append((str(py.relative_to(REPO_ROOT)), hits))
    if offenders:
        lines = ["studio/ leaked into a lower tier:"]
        for path, hits in offenders:
            for h in hits:
                lines.append(f"  {path}: {h}")
        raise AssertionError("\n".join(lines))


def test_studio_does_not_import_api_or_cli():
    """``studio/`` must not import from ``api/`` or ``cli/``."""
    if not STUDIO_DIR.is_dir():
        return  # skeleton not present yet
    offenders: list[tuple[str, list[str]]] = []
    for py in _walk(STUDIO_DIR):
        hits = _scan_file(py, _API_RE) + _scan_file(py, _CLI_RE)
        if hits:
            offenders.append((str(py.relative_to(REPO_ROOT)), hits))
    if offenders:
        lines = ["studio/ imports from a higher tier (api/ or cli/):"]
        for path, hits in offenders:
            for h in hits:
                lines.append(f"  {path}: {h}")
        raise AssertionError("\n".join(lines))


def test_packages_does_not_import_studio():
    """``packages/`` must not import from ``studio/`` (it is a lower tier)."""
    if not PACKAGES_DIR.is_dir():
        return
    offenders: list[tuple[str, list[str]]] = []
    for py in _walk(PACKAGES_DIR):
        hits = _scan_file(py, _STUDIO_RE)
        if hits:
            offenders.append((str(py.relative_to(REPO_ROOT)), hits))
    if offenders:
        lines = ["packages/ imports from studio/ (forbidden):"]
        for path, hits in offenders:
            for h in hits:
                lines.append(f"  {path}: {h}")
        raise AssertionError("\n".join(lines))


def test_packages_does_not_import_api_or_cli():
    """``packages/`` is below ``api/`` and ``cli/`` — must not import them."""
    if not PACKAGES_DIR.is_dir():
        return
    offenders: list[tuple[str, list[str]]] = []
    for py in _walk(PACKAGES_DIR):
        hits = _scan_file(py, _API_RE) + _scan_file(py, _CLI_RE)
        if hits:
            offenders.append((str(py.relative_to(REPO_ROOT)), hits))
    if offenders:
        lines = ["packages/ imports from api/ or cli/ (forbidden):"]
        for path, hits in offenders:
            for h in hits:
                lines.append(f"  {path}: {h}")
        raise AssertionError("\n".join(lines))


def test_core_does_not_import_legacy_api_studio():
    """``api/studio/`` is the legacy embedded prototype.

    Only ``api/app.py`` may include its router. This guard is preserved
    from the original ``test_studio_independence.py`` so the optional-
    embedded-section property of the legacy prototype is not broken.
    """
    offenders: list[tuple[str, list[str]]] = []
    for py in _walk(SRC_DIR):
        if _is_under(py, LEGACY_API_STUDIO_DIR):
            continue
        if py in LEGACY_API_STUDIO_ALLOWLIST:
            continue
        hits = _scan_file(py, _LEGACY_API_STUDIO_RE)
        if hits:
            offenders.append((str(py.relative_to(REPO_ROOT)), hits))
    if offenders:
        lines = ["legacy api/studio/ imports leaked into core framework:"]
        for path, hits in offenders:
            for h in hits:
                lines.append(f"  {path}: {h}")
        raise AssertionError("\n".join(lines))


def test_studio_subtree_exists():
    """Phase 0 skeleton: studio/ + its subpackages exist."""
    assert STUDIO_DIR.is_dir(), f"studio package missing: {STUDIO_DIR}"
    for sub in (
        "catalog",
        "identity",
        "editors",
        "sessions",
        "persistence",
        "persistence/viewer",
        "attach",
    ):
        path = STUDIO_DIR / sub
        assert path.is_dir(), f"studio/{sub}/ missing"
        assert (path / "__init__.py").is_file(), f"studio/{sub}/__init__.py missing"


def test_packages_subtree_exists():
    """Phase 0: packages/ split into 6 submodules."""
    assert PACKAGES_DIR.is_dir(), f"packages/ package missing: {PACKAGES_DIR}"
    for module in ("locations", "manifest", "walk", "resolve", "install", "slots"):
        path = PACKAGES_DIR / f"{module}.py"
        assert path.is_file(), f"packages/{module}.py missing"


def test_legacy_studio_touch_point_T1_preserved():
    """``api/app.py`` must still include the legacy studio router."""
    app_py = (SRC_DIR / "api" / "app.py").read_text(encoding="utf-8")
    assert "build_studio_router" in app_py, (
        "api/app.py must import + include build_studio_router (legacy "
        "studio prototype touch point)"
    )
    assert (
        "include_router(build_studio_router())" in app_py
    ), "api/app.py must call app.include_router(build_studio_router())"
