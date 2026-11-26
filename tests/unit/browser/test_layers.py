"""The layer map of ``browser/README.md``, enforced on the real import graph.

Runtime modules never import agent-facing ones (bar the listed exceptions),
the structured read imports runtime modules only, and every module of the
package is placed in a layer. Imports inside functions and under
``TYPE_CHECKING`` count.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "kohakuterrarium.browser"

RUNTIME = {
    "access",
    "downloads",
    "dialogs",
    "driver",
    "egress",
    "errors",
    "launch",
    "options",
    "policy",
    "processes",
    "profiles",
    "protocol",
    "proxy",
    "read",
    "secrets",
    "session.lifecycle",
    "session.reading",
    "session.sink",
    "session.types",
    "settle",
    "tabs",
    "tasks",
}
AGENT_FACING = {
    "actions",
    "hidden",
    "images",
    "observe",
    "refs",
    "results",
    "snapshot",
    "session.acting",
    "session.capture",
    "session.navigation",
    "session.observing",
    "session.tab_ops",
}
COMPOSITION = {"", "session", "session.assembly"}
KNOWN_EXCEPTIONS = {
    ("session.lifecycle", "observe"),
    ("session.lifecycle", "refs"),
}
STRUCTURED_READ = {"session.reading", "read"}


def short(module: str) -> str:
    return module[len(PREFIX) + 1 :] if module != PREFIX else ""


@pytest.fixture(scope="module")
def edges(monkeypatch_module) -> set[tuple[str, str]]:
    spec = importlib.util.spec_from_file_location(
        "dep_graph", REPO_ROOT / "scripts" / "dep_graph.py"
    )
    dep_graph = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dep_graph)
    monkeypatch_module.chdir(REPO_ROOT)
    facts, errors, modules = dep_graph.collect_facts()
    assert errors == []
    graph, _ = dep_graph.build_graph(
        facts, modules, include_in_function=True, skip_type_checking=False
    )
    return {
        (short(src), short(dst))
        for src, targets in graph.items()
        if src == PREFIX or src.startswith(PREFIX + ".")
        for dst in targets
        if dst == PREFIX or dst.startswith(PREFIX + ".")
    }


@pytest.fixture(scope="module")
def monkeypatch_module():
    patch = pytest.MonkeyPatch()
    yield patch
    patch.undo()


def test_every_module_is_placed_in_one_layer():
    root = REPO_ROOT / "src" / "kohakuterrarium" / "browser"
    found = set()
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found.add(".".join(parts))
    layers = [RUNTIME, AGENT_FACING, COMPOSITION]
    assert found == set().union(*layers)
    assert sum(len(layer) for layer in layers) == len(found)


def test_the_runtime_layer_does_not_import_the_agent_facing_layer(edges):
    crossing = {(s, d) for s, d in edges if s in RUNTIME and d in AGENT_FACING}
    assert crossing - KNOWN_EXCEPTIONS == set(), sorted(crossing - KNOWN_EXCEPTIONS)
    assert KNOWN_EXCEPTIONS <= crossing, "a listed exception is gone; drop it"


def test_the_structured_read_uses_the_runtime_layer_only(edges):
    used = {d for s, d in edges if s in STRUCTURED_READ}
    assert used, "the structured read imports nothing?"
    assert used <= RUNTIME | {"session.assembly"}, sorted(used - RUNTIME)
