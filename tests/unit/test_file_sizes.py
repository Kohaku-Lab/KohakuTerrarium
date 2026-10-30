"""Guard: source files stay under 1000 statements (hard); over 500 (soft) warns."""

import ast
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "kohakuterrarium"
VSCODE_SRC = ROOT / "extensions" / "vscode" / "src"
VSCODE_TESTS = ROOT / "extensions" / "vscode" / "test"

SOFT_CAP_STATEMENTS = 500
HARD_CAP_STATEMENTS = 1000
VSCODE_CAP_LINES = 600


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
def test_file_statement_caps(path):
    """Over the soft cap warns; over the hard cap fails."""
    rel = path.relative_to(SRC).as_posix()
    statements = count_statements(path.read_text(encoding="utf-8"))
    assert (
        statements <= HARD_CAP_STATEMENTS
    ), f"{rel} has {statements} statements (hard max {HARD_CAP_STATEMENTS})"
    if statements > SOFT_CAP_STATEMENTS:
        warnings.warn(
            f"{rel} has {statements} statements (soft cap {SOFT_CAP_STATEMENTS}): "
            "keep it only if it is one large coherent unit",
            stacklevel=1,
        )


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
