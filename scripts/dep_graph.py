"""
Build and analyze the runtime import dependency graph.

Usage:
    python scripts/dep_graph.py                # print stats + ASCII summary
    python scripts/dep_graph.py --dot          # output Graphviz DOT
    python scripts/dep_graph.py --plot         # render matplotlib plot
    python scripts/dep_graph.py --cycles       # find runtime SCC cycles
    python scripts/dep_graph.py --all          # everything
"""

import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path("src/kohakuterrarium")
PKG = "kohakuterrarium"


# ── Import graph extraction ──────────────────────────────────────────


def _module_name(path: Path) -> str:
    """Convert file path to module name."""
    rel = path.relative_to(ROOT.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def extract_imports(path: Path) -> list[tuple[str, str, bool]]:
    """Extract runtime imports from a Python file.

    Returns list of (from_module, to_module, is_type_checking).
    Skips TYPE_CHECKING-guarded imports.
    """
    try:
        source = path.read_text()
        tree = ast.parse(source)
    except Exception:
        return []

    from_mod = _module_name(path)
    results = []

    # Find TYPE_CHECKING blocks
    tc_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                start = node.lineno
                end = max(
                    (getattr(n, "end_lineno", 0) or getattr(n, "lineno", 0))
                    for n in ast.walk(node)
                    if hasattr(n, "lineno")
                )
                tc_ranges.append((start, end))
            elif isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                start = node.lineno
                end = max(
                    (getattr(n, "end_lineno", 0) or getattr(n, "lineno", 0))
                    for n in ast.walk(node)
                    if hasattr(n, "lineno")
                )
                tc_ranges.append((start, end))

    def in_tc(lineno: int) -> bool:
        return any(s <= lineno <= e for s, e in tc_ranges)

    # Also skip imports inside functions (they are lazy/optional)
    func_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            func_ranges.append((node.lineno, end))

    def in_func(lineno: int) -> bool:
        return any(s <= lineno <= e for s, e in func_ranges)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if not node.module.startswith(PKG):
                continue
            is_tc = in_tc(node.lineno)
            is_lazy = in_func(node.lineno)
            if is_lazy:
                continue  # skip lazy/in-function imports from runtime graph
            results.append((from_mod, node.module, is_tc))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PKG):
                    is_tc = in_tc(node.lineno)
                    is_lazy = in_func(node.lineno)
                    if is_lazy:
                        continue
                    results.append((from_mod, alias.name, is_tc))

    return results


def build_graph() -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Build the full import graph.

    Returns (runtime_edges, tc_edges, all_modules).
    """
    runtime: dict[str, set[str]] = defaultdict(set)
    tc_only: dict[str, set[str]] = defaultdict(set)
    all_mods: set[str] = set()

    for py_file in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        mod = _module_name(py_file)
        all_mods.add(mod)

        for from_mod, to_mod, is_tc in extract_imports(py_file):
            # Normalize: if to_mod is a sub-path, resolve to closest module
            all_mods.add(from_mod)
            all_mods.add(to_mod)
            if is_tc:
                tc_only[from_mod].add(to_mod)
            else:
                runtime[from_mod].add(to_mod)

    return runtime, tc_only, all_mods


# ── Analysis ─────────────────────────────────────────────────────────


def find_sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    """Find strongly connected components using Tarjan's algorithm."""
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(v: str):
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in graph.get(v, set()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            scc = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1:
                sccs.append(scc)

    for v in graph:
        if v not in index:
            strongconnect(v)
    for v in set().union(*graph.values()) - set(graph.keys()):
        pass  # leaf nodes, no SCC possible

    return sccs


def _short(mod: str) -> str:
    """Shorten module name for display."""
    return mod.replace("kohakuterrarium.", "kt.")


def _group(mod: str) -> str:
    """Get the top-level group for a module."""
    parts = mod.replace("kohakuterrarium.", "").split(".")
    return parts[0] if parts else mod


# ── Output formats ───────────────────────────────────────────────────


def print_stats(runtime: dict[str, set[str]], all_mods: set[str]):
    """Print summary statistics."""
    total_edges = sum(len(v) for v in runtime.values())
    sources = set(runtime.keys())
    targets = set().union(*runtime.values()) if runtime else set()

    # Fan-out (most imports)
    fan_out = sorted(
        [(mod, len(deps)) for mod, deps in runtime.items()],
        key=lambda x: -x[1],
    )

    # Fan-in (most imported by)
    fan_in: dict[str, int] = defaultdict(int)
    for src, dests in runtime.items():
        for d in dests:
            fan_in[d] += 1
    fan_in_sorted = sorted(fan_in.items(), key=lambda x: -x[1])

    print("=" * 70)
    print("DEPENDENCY GRAPH STATISTICS")
    print("=" * 70)
    print(f"Modules:      {len(all_mods)}")
    print(f"Runtime edges: {total_edges}")
    print(f"Sources (modules with imports): {len(sources)}")
    print(f"Targets (modules imported):     {len(targets)}")
    print()

    print("Top 15 fan-out (most imports):")
    for mod, count in fan_out[:15]:
        print(f"  {count:3d}  {_short(mod)}")
    print()

    print("Top 15 fan-in (most imported by):")
    for mod, count in fan_in_sorted[:15]:
        print(f"  {count:3d}  {_short(mod)}")
    print()

    # Group-level summary
    group_edges: dict[tuple[str, str], int] = defaultdict(int)
    for src, dests in runtime.items():
        sg = _group(src)
        for d in dests:
            dg = _group(d)
            if sg != dg:
                group_edges[(sg, dg)] += 1

    print("Cross-group edges:")
    for (sg, dg), count in sorted(group_edges.items(), key=lambda x: -x[1]):
        print(f"  {count:3d}  {sg} -> {dg}")


def print_cycles(runtime: dict[str, set[str]]):
    """Print runtime SCCs."""
    sccs = find_sccs(runtime)
    print()
    print("=" * 70)
    print("RUNTIME STRONGLY CONNECTED COMPONENTS (CYCLES)")
    print("=" * 70)
    if not sccs:
        print("None found. The runtime import graph is acyclic.")
    else:
        for i, scc in enumerate(sccs, 1):
            print(f"\nSCC #{i} ({len(scc)} modules):")
            for mod in sorted(scc):
                print(f"  {_short(mod)}")


def output_dot(runtime: dict[str, set[str]], all_mods: set[str]):
    """Output Graphviz DOT format."""
    # Group modules by top-level package
    groups: dict[str, list[str]] = defaultdict(list)
    for mod in all_mods:
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

    print("digraph kohakuterrarium {")
    print("  rankdir=LR;")
    print("  node [shape=box, fontsize=10, style=filled];")
    print('  edge [color="#666666", arrowsize=0.6];')
    print()

    for group, mods in sorted(groups.items()):
        color = colors.get(group, "#FFFFFF")
        print(f"  subgraph cluster_{group} {{")
        print(f'    label="{group}";')
        print(f"    style=filled;")
        print(f'    color="{color}";')
        for mod in sorted(mods):
            short = _short(mod)
            print(f'    "{short}";')
        print("  }")
        print()

    for src, dests in sorted(runtime.items()):
        for dest in sorted(dests):
            print(f'  "{_short(src)}" -> "{_short(dest)}";')

    print("}")


def render_plot(runtime: dict[str, set[str]], all_mods: set[str]):
    """Render a matplotlib plot of the group-level dependency graph."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import math
    except ImportError:
        print("matplotlib not installed. Install with: pip install matplotlib")
        return

    # Build group-level graph
    group_edges: dict[tuple[str, str], int] = defaultdict(int)
    group_sizes: dict[str, int] = defaultdict(int)
    for mod in all_mods:
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

    # Layout: circle
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

    # Draw edges
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
        # Edge label
        mx = (x1 + x2) / 2 + 0.15
        my = (y1 + y2) / 2 + 0.15
        ax.text(mx, my, str(count), fontsize=7, color="#888888", ha="center")

    # Draw nodes
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

    # Fan-in/fan-out stats per group
    group_fan_out: dict[str, int] = defaultdict(int)
    group_fan_in: dict[str, int] = defaultdict(int)
    for (sg, dg), count in group_edges.items():
        group_fan_out[sg] += count
        group_fan_in[dg] += count

    # Legend with stats
    legend_lines = ["Group  | Files | Fan-out | Fan-in"]
    for g in sorted(groups):
        fo = group_fan_out.get(g, 0)
        fi = group_fan_in.get(g, 0)
        legend_lines.append(f"{g:<15} {group_sizes[g]:>3}    {fo:>4}     {fi:>4}")

    ax.text(
        0.02,
        0.02,
        "\n".join(legend_lines),
        transform=ax.transAxes,
        fontsize=8,
        fontfamily="monospace",
        verticalalignment="bottom",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="white", alpha=0.9),
    )

    ax.set_xlim(-6, 6)
    ax.set_ylim(-6, 6)
    ax.axis("off")

    out_path = Path("plans/dep-graph.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Plot saved to {out_path}")

    # Also save a detailed module-level plot
    _render_detailed_plot(runtime, all_mods, group_sizes, colors_map)


def _render_detailed_plot(
    runtime: dict[str, set[str]],
    all_mods: set[str],
    group_sizes: dict[str, int],
    colors_map: dict[str, str],
):
    """Render a detailed module-level plot (may be dense)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import math

    # Filter to modules that have edges
    active_mods = set(runtime.keys()) | set().union(*runtime.values())

    # Group modules
    groups: dict[str, list[str]] = defaultdict(list)
    for mod in active_mods:
        groups[_group(mod)].append(mod)

    # Layout: group-based concentric rings
    positions: dict[str, tuple[float, float]] = {}
    group_list = sorted(groups.keys())
    n_groups = len(group_list)

    for gi, g in enumerate(group_list):
        mods = sorted(groups[g])
        base_angle = 2 * math.pi * gi / n_groups - math.pi / 2
        spread = 2 * math.pi / n_groups * 0.8
        for mi, mod in enumerate(mods):
            if len(mods) == 1:
                angle = base_angle
            else:
                angle = base_angle + spread * (mi / (len(mods) - 1) - 0.5)
            r = 5 + mi * 0.3
            positions[mod] = (math.cos(angle) * r, math.sin(angle) * r)

    fig, ax = plt.subplots(1, 1, figsize=(24, 24))
    ax.set_aspect("equal")
    ax.set_title(
        "KohakuTerrarium Detailed Module Dependency Graph",
        fontsize=16,
        fontweight="bold",
        pad=20,
    )

    # Draw edges (thin, low alpha)
    for src, dests in runtime.items():
        if src not in positions:
            continue
        for dest in dests:
            if dest not in positions:
                continue
            x1, y1 = positions[src]
            x2, y2 = positions[dest]
            sg, dg = _group(src), _group(dest)
            color = "#CC0000" if sg != dg else "#888888"
            alpha = 0.4 if sg != dg else 0.15
            ax.annotate(
                "",
                xy=(x2, y2),
                xytext=(x1, y1),
                arrowprops=dict(
                    arrowstyle="-|>",
                    color=color,
                    alpha=alpha,
                    lw=0.5,
                    connectionstyle="arc3,rad=0.05",
                ),
            )

    # Draw nodes
    for mod, (x, y) in positions.items():
        g = _group(mod)
        color = colors_map.get(g, "#CCCCCC")
        short = mod.replace("kohakuterrarium.", "").split(".")[-1]
        ax.plot(
            x,
            y,
            "o",
            color=color,
            markersize=8,
            markeredgecolor="black",
            markeredgewidth=0.5,
            zorder=5,
        )
        ax.text(x + 0.15, y + 0.15, short, fontsize=6, zorder=6, alpha=0.8)

    ax.set_xlim(-10, 10)
    ax.set_ylim(-10, 10)
    ax.axis("off")

    out_path = Path("plans/dep-graph-detailed.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Detailed plot saved to {out_path}")


# ── Main ─────────────────────────────────────────────────────────────


def main():
    args = set(sys.argv[1:])
    do_all = "--all" in args

    runtime, tc_only, all_mods = build_graph()

    if not args or do_all or "--stats" in args:
        print_stats(runtime, all_mods)

    if do_all or "--cycles" in args:
        print_cycles(runtime)

    if "--dot" in args:
        output_dot(runtime, all_mods)

    if do_all or "--plot" in args:
        render_plot(runtime, all_mods)


if __name__ == "__main__":
    main()
