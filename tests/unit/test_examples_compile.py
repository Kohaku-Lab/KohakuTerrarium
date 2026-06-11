"""Drift guard: every Python example must at least compile.

The examples are documentation — a stale import or signature in
``examples/code/`` is a doc bug users hit on day one. Full execution
needs live providers + installed packages, so the cheap invariant
pinned here is syntactic validity plus resolvable *kohakuterrarium*
imports (the example must not reference a module we deleted).
"""

import ast
import importlib.util
import py_compile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_FILES = sorted(
    list((REPO_ROOT / "examples" / "code").glob("*.py"))
    + list((REPO_ROOT / "examples" / "plugins").glob("*.py"))
)

# Third-party deps some examples need that are NOT framework deps —
# their imports are allowed to be missing in the test env.
_OPTIONAL_TOPLEVEL = {"discord"}


def _kt_imports(path: Path) -> list[str]:
    """Module paths imported from kohakuterrarium.* at module level."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == "kohakuterrarium":
                found.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "kohakuterrarium":
                    found.append(alias.name)
    return found


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.stem)
def test_example_compiles_and_imports_resolve(path):
    # 1. The file is valid Python.
    py_compile.compile(str(path), doraise=True)
    # 2. Every kohakuterrarium import target actually exists — a
    #    deleted/renamed module fails here, not on a user's machine.
    for module in _kt_imports(path):
        assert (
            importlib.util.find_spec(module) is not None
        ), f"{path.name} imports {module!r}, which does not exist"


def test_examples_were_discovered():
    # Guard the guard: an empty glob (moved dirs) must fail loudly.
    assert len(EXAMPLE_FILES) >= 15, [p.name for p in EXAMPLE_FILES]
