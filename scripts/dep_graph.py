"""Build and analyze the runtime import dependency graph for src/kohakuterrarium.

Modes:
    python scripts/dep_graph.py                  # stats summary
    python scripts/dep_graph.py --cycles         # find runtime SCCs (full graph)
    python scripts/dep_graph.py --lint-imports   # report in-function-import violations
    python scripts/dep_graph.py --dot            # Graphviz DOT
    python scripts/dep_graph.py --plot           # matplotlib plot
    python scripts/dep_graph.py --json           # JSON dump (graph + lint result)
    python scripts/dep_graph.py --fail           # exit non-zero on errors/cycles/violations
    python scripts/dep_graph.py --all            # stats + cycles + lint

Notes:
- All source files are read as UTF-8 (CP950 default on Windows would mask
  most files); read or parse failures surface immediately.
- The graph has two views:
    * module-level only — what eager runtime imports look like today.
    * full (--full or --cycles by default) — also includes function-local
      imports. Cycles hidden behind lazy imports show up here.
- The lint mode classifies every in-function import against pyproject.toml
  ([project].dependencies, [project.optional-dependencies]) and
  ``sys.stdlib_module_names``. Allowed cases (with reasons) live in
  ``scripts/dep_graph_allowlist.json``. Module-level ``__getattr__`` is a
  Python language feature, not a real lazy import — auto-exempt.
"""

import argparse
import ast
import json
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

ROOT = Path("src/kohakuterrarium")
PKG = "kohakuterrarium"
PYPROJECT = Path("pyproject.toml")
ALLOWLIST_PATH = Path("scripts/dep_graph_allowlist.json")


# ── Distribution-name → top-level-module-name mapping ───────────────

DIST_TO_TOP = {
    "pyyaml": "yaml",
    "ruamel.yaml": "ruamel",
    "python-dotenv": "dotenv",
    "gitpython": "git",
    "uvicorn": "uvicorn",
    "pymupdf": "fitz",
    "pywebview": "webview",
    "pywinpty": "winpty",
    "discord.py": "discord",
    "pillow": "PIL",
    "sentence-transformers": "sentence_transformers",
}


# Imports that the linter accepts even though they aren't in pyproject:
# stdlib-but-platform-only and de-facto optional/transitive backends.
PLATFORM_OPTIONAL = {
    "AppKit",
    "Cocoa",
    "Foundation",
    "objc",
    "fcntl",
    "pty",
    "termios",
    "winreg",
    "watchfiles",
}


# ── pyproject parsing ───────────────────────────────────────────────


def _spec_to_top(spec: str) -> str:
    name = spec.split(";")[0].split("[")[0]
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if sep in name:
            name = name.split(sep, 1)[0]
    name = name.strip().lower()
    return DIST_TO_TOP.get(name, name.replace("-", "_"))


def load_dependencies() -> tuple[set[str], set[str]]:
    """Return (required_top_levels, optional_top_levels)."""
    if not PYPROJECT.exists():
        return set(), set()
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    proj = data.get("project", {}) or {}
    required = {_spec_to_top(s) for s in proj.get("dependencies", []) or []}
    optional: set[str] = set()
    for group in (proj.get("optional-dependencies") or {}).values():
        for s in group:
            optional.add(_spec_to_top(s))
    return required, optional


def classify(root: str, required: set[str], optional: set[str]) -> str:
    if root == PKG:
        return "internal"
    # Platform-only (incl. stdlib platform modules like fcntl/pty/termios):
    # check before generic stdlib so a POSIX-only stdlib import is treated
    # as optional-platform rather than an unguarded stdlib violation.
    if root in PLATFORM_OPTIONAL:
        return "optional-platform"
    if root in sys.stdlib_module_names:
        return "stdlib"
    if root in required:
        return "required-external"
    if root in optional:
        return "optional-declared"
    return "unknown"


# ── AST extraction ──────────────────────────────────────────────────


def _module_name(path: Path) -> str:
    rel = path.relative_to(ROOT.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _scan_ranges(tree: ast.AST):
    tc_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_tc:
                end = max(
                    (getattr(n, "end_lineno", 0) or getattr(n, "lineno", 0))
                    for n in ast.walk(node)
                )
                tc_ranges.append((node.lineno, end))

    func_ranges: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            func_ranges.append((node.lineno, end, node.name))

    try_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            handlers_catch = False
            for h in node.handlers:
                if h.type is None:
                    handlers_catch = True
                elif isinstance(h.type, ast.Name) and h.type.id in (
                    "ImportError",
                    "ModuleNotFoundError",
                    "Exception",
                ):
                    handlers_catch = True
                elif isinstance(h.type, ast.Tuple):
                    for el in h.type.elts:
                        if isinstance(el, ast.Name) and el.id in (
                            "ImportError",
                            "ModuleNotFoundError",
                        ):
                            handlers_catch = True
            if handlers_catch:
                end = node.end_lineno or node.lineno
                try_ranges.append((node.lineno, end))

    return tc_ranges, func_ranges, try_ranges


def extract_imports(tree: ast.AST, from_mod: str, file_path: Path) -> list[dict]:
    tc, funcs, tries = _scan_ranges(tree)

    def in_tc(line: int) -> bool:
        return any(s <= line <= e for s, e in tc)

    def enclosing(line: int) -> str | None:
        best = None
        for s, e, name in funcs:
            if s <= line <= e and (best is None or s > best[0]):
                best = (s, e, name)
        return best[2] if best else None

    def in_try(line: int) -> bool:
        return any(s <= line <= e for s, e in tries)

    facts: list[dict] = []
    file_str = str(file_path).replace("\\", "/")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.append(
                    {
                        "from_mod": from_mod,
                        "target": alias.name,
                        "line": node.lineno,
                        "type_checking": in_tc(node.lineno),
                        "in_function": enclosing(node.lineno),
                        "try_import_error": in_try(node.lineno),
                        "file": file_str,
                        "stmt": f"import {alias.name}",
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            target = node.module
            names = ", ".join(a.name for a in node.names)
            facts.append(
                {
                    "from_mod": from_mod,
                    "target": target,
                    "line": node.lineno,
                    "type_checking": in_tc(node.lineno),
                    "in_function": enclosing(node.lineno),
                    "try_import_error": in_try(node.lineno),
                    "file": file_str,
                    "stmt": f"from {target} import {names}",
                }
            )
    return facts


def collect_facts() -> tuple[list[dict], list[tuple[str, str]], set[str]]:
    """Walk src/. Return (facts, parse_errors, all_modules)."""
    facts: list[dict] = []
    parse_errors: list[tuple[str, str]] = []
    all_modules: set[str] = set()
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        all_modules.add(_module_name(path))
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as e:
            parse_errors.append((str(path).replace("\\", "/"), repr(e)))
            continue
        facts.extend(extract_imports(tree, _module_name(path), path))
    return facts, parse_errors, all_modules


# ── Graph construction ─────────────────────────────────────────────


def _resolve_internal(target: str, all_modules: set[str]) -> str | None:
    if not target.startswith(PKG):
        return None
    parts = target.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in all_modules:
            return candidate
        parts.pop()
    return None


def build_graph(
    facts: list[dict],
    all_modules: set[str],
    *,
    include_in_function: bool,
    skip_type_checking: bool = True,
) -> tuple[dict[str, set[str]], dict[tuple[str, str], list[dict]]]:
    edges_meta: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in facts:
        if skip_type_checking and f["type_checking"]:
            continue
        if not include_in_function and f["in_function"] is not None:
            # The exception for module-level __getattr__: it is a Python
            # language feature for module-attribute lazy export — count it
            # as a runtime edge so cycle detection sees the dependency.
            if f["in_function"] != "__getattr__":
                continue
        target = _resolve_internal(f["target"], all_modules)
        if target is None or target == f["from_mod"]:
            continue
        edges_meta[(f["from_mod"], target)].append(f)
    runtime: dict[str, set[str]] = defaultdict(set)
    for s, t in edges_meta:
        runtime[s].add(t)
    return runtime, edges_meta


# ── Tarjan SCCs ─────────────────────────────────────────────────────


def find_sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10_000))
    idx: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = [0]

    def strongconnect(v: str) -> None:
        idx[v] = counter[0]
        low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in idx:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(comp)

    for v in list(graph.keys()):
        if v not in idx:
            strongconnect(v)
    return sccs


def sample_cycle_path(scc: list[str], graph: dict[str, set[str]]) -> list[str]:
    """Return one cycle through (a subset of) the SCC, starting and ending at the same node."""
    nodes = set(scc)
    start = sorted(scc)[0]
    path = [start]
    visited = {start}
    cur = start
    while True:
        nxt: str | None = None
        for w in sorted(graph.get(cur, ())):
            if w not in nodes:
                continue
            if w == start and len(path) > 1:
                path.append(start)
                return path
            if w not in visited:
                nxt = w
                break
        if nxt is None:
            # dead-end — fall back to the cycle around the smallest 2-cycle
            for w in sorted(graph.get(cur, ())):
                if w in nodes and w in path:
                    path.append(w)
                    return path
            return path
        path.append(nxt)
        visited.add(nxt)
        cur = nxt


# ── Allowlist ──────────────────────────────────────────────────────


def load_allowlist() -> list[dict]:
    if not ALLOWLIST_PATH.exists():
        return []
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))


def _matches(entry: dict, fact: dict) -> bool:
    if entry.get("file") != fact["file"]:
        return False
    if "line" in entry and entry["line"] != fact["line"]:
        return False
    if "function" in entry and entry["function"] != fact["in_function"]:
        return False
    if "target" in entry and entry["target"] != fact["target"]:
        return False
    return True


# ── Tier rules (Phase 0 of studio-cleanup refactor) ─────────────────
# Phase 0 introduces three tier separations:
#
# - ``packages/`` is a low-tier library (peer to core / bootstrap /
#   terrarium); it cannot reach into ``studio/``, ``api/``, or ``cli/``.
# - ``studio/`` is the new orchestration tier; lower tiers (``core``,
#   ``bootstrap``, ``compose``, ``terrarium``, ``packages``) cannot
#   import it, and ``studio/`` cannot import ``api/`` or ``cli/``.
# - ``api/`` and ``cli/`` are top tiers — every other tier is below.
#
# Lower tiers below ``studio/`` are peers: the existing dependency
# edges between e.g. ``core`` and ``bootstrap`` are not regulated
# here, only the new tier separations.

# Mapping from tier root → set of tier roots it MAY NOT import.
FORBIDDEN_TIER_TARGETS: dict[str, set[str]] = {
    "core": {"studio", "api", "cli"},
    "bootstrap": {"studio", "api", "cli"},
    "compose": {"studio", "api", "cli"},
    "terrarium": {"studio", "api", "cli"},
    "packages": {"studio", "api", "cli"},
    "studio": {"api", "cli"},
}


def _tier_for(module: str) -> str | None:
    """Return the tier root for ``module`` or ``None`` if unmanaged."""
    if not module.startswith(f"{PKG}."):
        return None
    parts = module.split(".")
    if len(parts) < 2:
        return None
    return parts[1]


def check_tier_violations(
    runtime: dict[str, set[str]],
) -> list[tuple[str, str, str, str]]:
    """Return (src, dst, src_tier, dst_tier) for each forbidden tier import."""
    violations: list[tuple[str, str, str, str]] = []
    for src, targets in runtime.items():
        src_tier = _tier_for(src)
        if src_tier is None or src_tier not in FORBIDDEN_TIER_TARGETS:
            continue
        forbidden = FORBIDDEN_TIER_TARGETS[src_tier]
        for dst in targets:
            dst_tier = _tier_for(dst)
            if dst_tier is None or dst_tier == src_tier:
                continue
            if dst_tier in forbidden:
                violations.append((src, dst, src_tier, dst_tier))
    return violations


# ── Lint ───────────────────────────────────────────────────────────


def lint_imports(
    facts: list[dict],
    required: set[str],
    optional: set[str],
    allowlist: list[dict] | None = None,
) -> tuple[list[tuple[dict, str]], list[tuple[dict, str]]]:
    """Return (violations, allowed)."""
    if allowlist is None:
        allowlist = load_allowlist()
    violations: list[tuple[dict, str]] = []
    allowed: list[tuple[dict, str]] = []
    for f in facts:
        if f["in_function"] is None:
            continue
        if f["type_checking"]:
            continue
        # Module-level __getattr__ is a Python language feature, not an
        # in-function import.
        if f["in_function"] == "__getattr__":
            allowed.append((f, "module-level __getattr__"))
            continue
        root = f["target"].split(".")[0]
        cls = classify(root, required, optional)
        guarded = f["try_import_error"]
        on_allowlist = any(_matches(e, f) for e in allowlist)
        if cls == "optional-declared" or cls == "optional-platform":
            allowed.append((f, f"{cls} (auto-allowed)"))
        elif on_allowlist:
            allowed.append((f, "allowlisted"))
        elif cls == "required-external" and guarded:
            allowed.append((f, "required-external in try/except ImportError"))
        elif cls == "internal":
            violations.append((f, "internal kohakuterrarium.* in-function import"))
        elif cls == "required-external":
            violations.append(
                (
                    f,
                    "required external in-function import (not allowlisted, not guarded)",
                )
            )
        elif cls == "stdlib":
            violations.append((f, "stdlib in-function import (not allowlisted)"))
        else:
            violations.append((f, f"unknown classification: {root}"))
    return violations, allowed


# ── Reports ─────────────────────────────────────────────────────────


def _short(mod: str) -> str:
    return mod.replace(f"{PKG}.", "kt.")


def _group(mod: str) -> str:
    parts = mod.replace(f"{PKG}.", "").split(".")
    return parts[0] if parts else mod


def report_parse_errors(parse_errors, out=sys.stdout) -> None:
    if not parse_errors:
        return
    print("=" * 70, file=out)
    print(f"PARSE ERRORS ({len(parse_errors)})", file=out)
    print("=" * 70, file=out)
    for p, msg in parse_errors:
        print(f"  {p}: {msg}", file=out)


def report_stats(runtime, all_modules, out=sys.stdout) -> None:
    total_edges = sum(len(v) for v in runtime.values())
    sources = set(runtime.keys())
    targets: set[str] = set().union(*runtime.values()) if runtime else set()

    fan_out = sorted(((m, len(d)) for m, d in runtime.items()), key=lambda x: -x[1])
    fan_in: dict[str, int] = defaultdict(int)
    for src, dests in runtime.items():
        for d in dests:
            fan_in[d] += 1
    fan_in_sorted = sorted(fan_in.items(), key=lambda x: -x[1])

    print("=" * 70, file=out)
    print("DEPENDENCY GRAPH STATISTICS", file=out)
    print("=" * 70, file=out)
    print(f"Modules:      {len(all_modules)}", file=out)
    print(f"Runtime edges: {total_edges}", file=out)
    print(f"Sources (modules with imports): {len(sources)}", file=out)
    print(f"Targets (modules imported):     {len(targets)}", file=out)
    print(file=out)
    print("Top 15 fan-out (most imports):", file=out)
    for mod, count in fan_out[:15]:
        print(f"  {count:3d}  {_short(mod)}", file=out)
    print(file=out)
    print("Top 15 fan-in (most imported by):", file=out)
    for mod, count in fan_in_sorted[:15]:
        print(f"  {count:3d}  {_short(mod)}", file=out)
    print(file=out)

    group_edges: dict[tuple[str, str], int] = defaultdict(int)
    for src, dests in runtime.items():
        sg = _group(src)
        for d in dests:
            dg = _group(d)
            if sg != dg:
                group_edges[(sg, dg)] += 1
    print("Cross-group edges:", file=out)
    for (sg, dg), count in sorted(group_edges.items(), key=lambda x: -x[1]):
        print(f"  {count:3d}  {sg} -> {dg}", file=out)


def report_cycles(sccs, edges_meta, out=sys.stdout) -> None:
    print("=" * 70, file=out)
    print("RUNTIME STRONGLY CONNECTED COMPONENTS (CYCLES)", file=out)
    print("=" * 70, file=out)
    if not sccs:
        print("None found. The runtime import graph is acyclic.", file=out)
        return
    sccs_sorted = sorted(sccs, key=lambda s: (len(s), sorted(s)))
    for i, scc in enumerate(sccs_sorted, 1):
        print(f"\nSCC #{i} ({len(scc)} modules):", file=out)
        for mod in sorted(scc):
            print(f"  {_short(mod)}", file=out)
        sub_graph: dict[str, set[str]] = defaultdict(set)
        for s, t in edges_meta:
            if s in scc and t in scc:
                sub_graph[s].add(t)
        path = sample_cycle_path(scc, sub_graph)
        if len(path) > 1:
            print("  sample cycle:", file=out)
            for a, b in zip(path[:-1], path[1:]):
                fact = edges_meta.get((a, b), [None])[0]
                if fact is None:
                    continue
                print(f"    {_short(a)} -> {_short(b)}", file=out)
                print(f"      {fact['file']}:{fact['line']}  {fact['stmt']}", file=out)


def report_tier_violations(
    tier_violations: list[tuple[str, str, str, str]], out=sys.stdout
) -> None:
    print("=" * 70, file=out)
    print("TIER VIOLATIONS", file=out)
    print("=" * 70, file=out)
    if not tier_violations:
        print("None — all imports respect the tier order.", file=out)
        return
    for src, dst, src_tier, dst_tier in tier_violations:
        print(
            f"  {_short(src)}  ->  {_short(dst)}  "
            f"({src_tier} importing higher tier {dst_tier})",
            file=out,
        )


def report_lint(violations, allowed, out=sys.stdout) -> None:
    print("=" * 70, file=out)
    print("IMPORT HYGIENE", file=out)
    print("=" * 70, file=out)
    print(f"  In-function imports allowed:    {len(allowed)}", file=out)
    print(f"  In-function imports violating: {len(violations)}", file=out)
    if violations:
        print(file=out)
        print("VIOLATIONS:", file=out)
        for f, why in violations:
            print(f"  {f['file']}:{f['line']}  ({f['in_function']})", file=out)
            print(f"    {f['stmt']}", file=out)
            print(f"    reason: {why}", file=out)


# ── DOT and plot (kept from previous script, unchanged behavior) ─────


def output_dot(runtime, all_modules, out=sys.stdout) -> None:
    groups: dict[str, list[str]] = defaultdict(list)
    for mod in all_modules:
        groups[_group(mod)].append(mod)
    colors = {
        "core": "#E8F5E9",
        "builtins": "#E3F2FD",
        "bootstrap": "#FFF3E0",
        "terrarium": "#FCE4EC",
        "modules": "#F3E5F5",
        "llm": "#E0F7FA",
        "parsing": "#FFF9C4",
        "prompt": "#F1F8E9",
        "serving": "#FFEBEE",
        "session": "#E8EAF6",
        "commands": "#EFEBE9",
        "testing": "#ECEFF1",
        "utils": "#F5F5F5",
    }
    print("digraph kohakuterrarium {", file=out)
    print("  rankdir=LR;", file=out)
    print("  node [shape=box, fontsize=10, style=filled];", file=out)
    print('  edge [color="#666666", arrowsize=0.6];', file=out)
    for group, mods in sorted(groups.items()):
        color = colors.get(group, "#FFFFFF")
        print(f"  subgraph cluster_{group} {{", file=out)
        print(f'    label="{group}";', file=out)
        print("    style=filled;", file=out)
        print(f'    color="{color}";', file=out)
        for mod in sorted(mods):
            print(f'    "{_short(mod)}";', file=out)
        print("  }", file=out)
    for src, dests in sorted(runtime.items()):
        for dest in sorted(dests):
            print(f'  "{_short(src)}" -> "{_short(dest)}";', file=out)
    print("}", file=out)


def render_plot(runtime, all_modules) -> None:
    try:
        import math

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed. Install with: pip install matplotlib")
        return

    group_edges: dict[tuple[str, str], int] = defaultdict(int)
    group_sizes: dict[str, int] = defaultdict(int)
    for mod in all_modules:
        group_sizes[_group(mod)] += 1
    for src, dests in runtime.items():
        sg = _group(src)
        for d in dests:
            dg = _group(d)
            if sg != dg:
                group_edges[(sg, dg)] += 1

    groups = sorted(group_sizes.keys())
    n = len(groups)

    colors_map = {
        "core": "#4CAF50",
        "builtins": "#2196F3",
        "bootstrap": "#FF9800",
        "terrarium": "#E91E63",
        "modules": "#9C27B0",
        "llm": "#00BCD4",
        "parsing": "#FFEB3B",
        "prompt": "#8BC34A",
        "serving": "#F44336",
        "session": "#3F51B5",
        "commands": "#795548",
        "testing": "#607D8B",
        "utils": "#9E9E9E",
        "builtin_skills": "#CDDC39",
    }

    positions = {}
    for i, g in enumerate(groups):
        angle = 2 * math.pi * i / n - math.pi / 2
        positions[g] = (math.cos(angle) * 4, math.sin(angle) * 4)

    fig, ax = plt.subplots(1, 1, figsize=(16, 16))
    ax.set_aspect("equal")
    ax.set_title(
        "KohakuTerrarium Module Dependency Graph (group level)",
        fontsize=16,
        fontweight="bold",
        pad=20,
    )
    for (sg, dg), count in group_edges.items():
        x1, y1 = positions[sg]
        x2, y2 = positions[dg]
        alpha = min(0.3 + count * 0.05, 0.9)
        width = min(0.5 + count * 0.3, 4.0)
        ax.annotate(
            "",
            xy=(x2, y2),
            xytext=(x1, y1),
            arrowprops=dict(
                arrowstyle="-|>",
                color="#555555",
                alpha=alpha,
                lw=width,
                connectionstyle="arc3,rad=0.1",
            ),
        )
        mx = (x1 + x2) / 2 + 0.15
        my = (y1 + y2) / 2 + 0.15
        ax.text(mx, my, str(count), fontsize=7, color="#888888", ha="center")
    for g in groups:
        x, y = positions[g]
        size = group_sizes[g]
        radius = 0.3 + size * 0.02
        color = colors_map.get(g, "#CCCCCC")
        circle = plt.Circle((x, y), radius, color=color, ec="black", lw=1.5, zorder=5)
        ax.add_patch(circle)
        ax.text(
            x,
            y,
            f"{g}\n({size})",
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
            zorder=6,
        )
    ax.set_xlim(-6, 6)
    ax.set_ylim(-6, 6)
    ax.axis("off")
    out_path = Path("plans/dep-graph.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Plot saved to {out_path}")


# ── CLI ─────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stats", action="store_true", help="print graph statistics")
    p.add_argument("--cycles", action="store_true", help="report runtime SCCs")
    p.add_argument(
        "--lint-imports",
        action="store_true",
        help="report in-function-import policy violations",
    )
    p.add_argument(
        "--module-only",
        action="store_true",
        help="build graph from module-level imports only (ignore in-function imports)",
    )
    p.add_argument("--dot", action="store_true", help="emit Graphviz DOT")
    p.add_argument("--plot", action="store_true", help="render matplotlib plot")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument(
        "--fail",
        action="store_true",
        help="exit non-zero on parse errors / cycles / lint violations",
    )
    p.add_argument("--all", action="store_true", help="stats + cycles + lint")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    facts, parse_errors, all_modules = collect_facts()
    required, optional = load_dependencies()

    include_lazy = not args.module_only
    runtime, edges_meta = build_graph(
        facts, all_modules, include_in_function=include_lazy
    )
    sccs = find_sccs(runtime)

    violations: list[tuple[dict, str]] = []
    allowed: list[tuple[dict, str]] = []
    if args.lint_imports or args.all or args.fail:
        violations, allowed = lint_imports(facts, required, optional)

    tier_violations = check_tier_violations(runtime)

    show_stats = (
        args.stats
        or args.all
        or (
            not any(
                (
                    args.cycles,
                    args.lint_imports,
                    args.dot,
                    args.plot,
                    args.json,
                    args.fail,
                    args.module_only,
                )
            )
        )
    )

    report_parse_errors(parse_errors)
    if show_stats:
        report_stats(runtime, all_modules)
    if args.cycles or args.all or args.fail:
        report_cycles(sccs, edges_meta)
    if args.lint_imports or args.all or args.fail:
        report_lint(violations, allowed)
        report_tier_violations(tier_violations)
    if args.dot:
        output_dot(runtime, all_modules)
    if args.plot or args.all:
        render_plot(runtime, all_modules)
    if args.json:
        payload = {
            "modules": sorted(all_modules),
            "edges": [
                {"from": s, "to": t, "count": len(edges_meta[(s, t)])}
                for (s, t) in sorted(edges_meta.keys())
            ],
            "sccs": [sorted(c) for c in sccs],
            "violations": [{"fact": f, "reason": r} for f, r in violations],
            "parse_errors": [{"file": f, "msg": m} for f, m in parse_errors],
        }
        print(json.dumps(payload, indent=2))

    if args.fail:
        if parse_errors:
            return 2
        if sccs:
            return 3
        if violations:
            return 4
        if tier_violations:
            return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
