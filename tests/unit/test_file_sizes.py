"""Guard: no source file exceeds 500 statements (soft) or 1000 (hard)."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "kohakuterrarium"
VSCODE_SRC = ROOT / "extensions" / "vscode" / "src"
VSCODE_TESTS = ROOT / "extensions" / "vscode" / "test"

SOFT_CAP_STATEMENTS = 500
HARD_CAP_STATEMENTS = 1000
VSCODE_CAP_LINES = 600

# Files allowed above the soft cap until their part-by-part refactor lands.
# An entry is removed when its file is split; never add one for new code.
ALLOWLIST_SOFT = {
    # Single TUI session-state class awaiting a pseudo-class package split.
    "builtins/tui/session.py",
    # Rich CLI app class awaiting a pseudo-class package split.
    "builtins/cli_rich/app.py",
}


def count_statements(source: str) -> int:
    """Count AST statements, excluding imports and docstrings.

    Blank lines, comments, docstrings and multi-line literals cost nothing, so
    the cap measures logic, not formatting.
    """
    total = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        total += 1
    return total


def _all_py_files():
    for p in SRC.rglob("*.py"):
        yield p


def _all_vscode_files():
    for root in (VSCODE_SRC, VSCODE_TESTS):
        for suffix in ("*.js", "*.cjs", "*.mjs"):
            yield from root.rglob(suffix)


@pytest.mark.parametrize(
    "path", list(_all_py_files()), ids=lambda p: str(p.relative_to(SRC))
)
def test_file_under_soft_statement_cap(path):
    rel = path.relative_to(SRC).as_posix()
    statements = count_statements(path.read_text(encoding="utf-8"))
    if rel in ALLOWLIST_SOFT:
        assert statements <= HARD_CAP_STATEMENTS, (
            f"{rel} has {statements} statements (allowlisted but max "
            f"{HARD_CAP_STATEMENTS})"
        )
    else:
        assert (
            statements <= SOFT_CAP_STATEMENTS
        ), f"{rel} has {statements} statements (max {SOFT_CAP_STATEMENTS})"


@pytest.mark.parametrize(
    "path", list(_all_py_files()), ids=lambda p: str(p.relative_to(SRC))
)
def test_file_under_hard_statement_cap(path):
    statements = count_statements(path.read_text(encoding="utf-8"))
    assert statements <= HARD_CAP_STATEMENTS, (
        f"{path.relative_to(SRC)} has {statements} statements "
        f"(hard max {HARD_CAP_STATEMENTS})"
    )


def test_allowlist_entries_still_exceed_the_soft_cap():
    stale = [
        rel
        for rel in sorted(ALLOWLIST_SOFT)
        if count_statements((SRC / rel).read_text(encoding="utf-8"))
        <= SOFT_CAP_STATEMENTS
    ]
    assert not stale, f"remove from ALLOWLIST_SOFT: {stale}"


@pytest.mark.parametrize(
    "path",
    list(_all_vscode_files()),
    ids=lambda p: str(p.relative_to(ROOT / "extensions" / "vscode")),
)
def test_vscode_file_under_line_cap(path):
    lines = len(path.read_text(encoding="utf-8").splitlines())
    rel = path.relative_to(ROOT / "extensions" / "vscode")
    assert lines <= VSCODE_CAP_LINES, f"{rel} is {lines} lines (max {VSCODE_CAP_LINES})"


def test_statement_counter_ignores_docstrings_imports_and_formatting():
    source = (
        '"""Module doc."""\n'
        "import os\n"
        "from sys import path\n"
        "\n"
        "# comment\n"
        "DATA = {\n"
        '    "a": 1,\n'
        '    "b": 2,\n'
        "}\n"
        "\n"
        "def f():\n"
        '    """Doc."""\n'
        "    x = 1\n"
        "    return x\n"
    )
    # DATA assignment, def, x assignment, return
    assert count_statements(source) == 4
