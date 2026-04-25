"""Guard the dependency-graph linter.

Re-runs the same checks the CI lint job runs:
1. No file fails to read or parse.
2. The runtime import graph (including function-local imports) is acyclic.
3. Every in-function import is either auto-allowed (optional/platform/
   try-except ImportError) or explicitly listed in
   ``scripts/dep_graph_allowlist.json`` with a reason.

The script lives outside ``src/``; load it by path so the test runs from
the in-place layout without requiring a pip install of ``scripts``.
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "dep_graph.py"


def _load_dep_graph():
    spec = importlib.util.spec_from_file_location("dep_graph", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dep_graph_passes_all_gates(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    dep_graph = _load_dep_graph()

    facts, parse_errors, all_modules = dep_graph.collect_facts()
    assert parse_errors == [], f"unexpected parse errors: {parse_errors}"

    runtime, _ = dep_graph.build_graph(facts, all_modules, include_in_function=True)
    sccs = dep_graph.find_sccs(runtime)
    assert sccs == [], (
        "runtime import graph has cycles; run "
        "`python scripts/dep_graph.py --cycles` for sample paths"
    )

    required, optional = dep_graph.load_dependencies()
    violations, _allowed = dep_graph.lint_imports(facts, required, optional)
    assert violations == [], (
        "in-function-import policy violations; run "
        "`python scripts/dep_graph.py --lint-imports` for the full list"
    )
