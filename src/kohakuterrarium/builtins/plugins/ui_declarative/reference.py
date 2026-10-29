"""The inline-UI part of ``## Presenting your answer`` (``ui.declarative``).

The component list is generated from ``kt_ui/components.py`` so the prompt
cannot drift from what the validator accepts.
"""

from kohakuterrarium.builtins.kt_ui.components import COMPONENTS, Prop
from kohakuterrarium.builtins.kt_ui.markup_lex import RAW_CHILD, TEXT_CHILD

_INTRO = """### Inline components

This chat draws UI components that you write directly in your reply. Use them in your normal answers whenever the content has shape, so the user sees it instead of reading it:
- comparing options, items or versions → `table` (`sortable`, `filter`, column formats for money, percentages and dates)
- numbers, shares and trends → `chart`; a trend beside a figure → `stat` with `trend`; a level against a limit → `chart kind="gauge"`
- structure, flow, architecture, steps, sequences → `diagram` (Mermaid source), in place of ASCII art or a Mermaid code block
- key figures and results → `stat`, `kv`, `badge`
- warnings, tips and key takeaways → `callout`; a cited passage → `quote`
- a procedure, a plan or where a task stands → `steps`; a history or schedule → `timeline`
- progress toward a goal → `progress`
- file trees and hierarchies → `tree`
- optional detail the user may open → `accordion`
- alternatives or several views of one thing → `tabs`
- what-if values and estimates → `slider` or `number` with `let` values
- formulas and equations → `math` (LaTeX)
- links to docs, sources and tools → `link`; a command or value to paste → `copy`
- screenshots and image sets → `gallery`; small markers → `icon`
- options with trade-offs → `compare` (pros, cons, verdict)
- practice and self-checks → `quiz`; a time box → `timer`; a score the user gives → `rating`
- next steps the user can pick → `button send="…"`; a click sends that text as the user's next message
Mix components with Markdown prose; one reply can hold several `<ui>` blocks."""
_HTML_LINE = "- a custom visual the components cannot express, such as a styled illustration, an SVG drawing or a one-off widget → `html` (a sandboxed frame with no network: inline every script, style and image)"
_MIX_LINE = (
    "Mix components with Markdown prose; one reply can hold several `<ui>` blocks."
)

_SYNTAX = """#### Writing a block
- A block starts with `<ui>` at the start of a line and ends with `</ui>`. Root attributes: `title="…"`, `layout="page"` for a wide view.
- `<state a={1} b="x"/>` declares values that inputs change; `<let total={a * 2}/>` declares computed values. Both go directly inside `<ui>`.
- Attributes: `name="text"` (may contain `{expr}`), `name={expr}` (a number, `[list]`, `{key: value}` object, or an expression over state), or a bare `name` for true.
- Text between tags is content: `<text>Markdown with {expr}</text>`, `<stat label="Each">{fmt(each, 2)}</stat>`, `<button send="…">Label</button>`. `code`, `diagram`, `math`, `copy` and `html` take raw text. Write `{{` for a literal brace.
- List components take their entries as attributes, never as child tags: `items={[…]}` (`steps`, `timeline`, `tree`, `gallery`, `compare`, `list`), `fields={[…]}` (`kv`), `columns={[…]}` and `rows={[…]}` (`table`), `options={[…]}` (`select`, `radio`, `quiz`).
- Several top-level components stack vertically."""

_EXPRESSIONS = """#### Expressions
Numbers, strings, `true false null`, `[lists]`, `{key: value}`; names from state, `let` and `each` (`index` too); `.key` and `[i]`; `+ - * / % **`, comparisons, `and or not`, `a ? b : c`. Functions: round floor ceil abs min max sum avg len pow sqrt clamp fmt pct range join keys values str num. `fmt(x, 2)` adds thousands separators; `pct(0.25)` is `25%`."""

_EXAMPLE = """#### Example
<ui title="Plan cost">
<state seats={8} plan="team"/>
<let price={plan == "team" ? 29 : 49}/>
<grid columns={2}>
  <slider label="Seats" bind="seats" min={1} max={50}/>
  <radio label="Plan" bind="plan" options={["team", "business"]}/>
</grid>
<stat label="Monthly">${fmt(seats * price)}</stat>
<chart kind="bar" labels={["Team", "Business"]} series={[{name: "per month", data: [seats * 29, seats * 49]}]}/>
<diagram>
graph LR
  Seats --> Price --> Invoice
</diagram>
<button send="Go with the {plan} plan for {seats} seats">Choose this</button>
</ui>"""

_ITEM_HINTS = {
    "options": "[strings or {value, label}]",
    "fields": "[{label, value}]",
    "columns": "[keys or {key, label, align, format: text|number|percent|currency|date, digits, currency}]",
    "rows": "[objects or arrays]",
    "series": "[{name, data}] (scatter data: [[x, y]]; heatmap: one series per row; gauge: data [value])",
    "items": "[…]",
    "trend": "[numbers] (a sparkline)",
}
_NODE_ITEM_HINTS = {
    ("steps", "items"): "[strings or {title, detail, status: done|current|todo}]",
    ("timeline", "items"): "[strings or {title, date, detail, tone}]",
    ("tree", "items"): "[strings or {label, children, badge, open}]",
    ("gallery", "items"): "[src strings or {src, alt, caption}]",
    ("compare", "items"): "[{title, pros: [text], cons: [text], verdict}]",
}
_HIDDEN = {"button": {"send", "message", "data"}, "form": {"send", "message", "data"}}


def _prop_text(component: str, name: str, prop: Prop) -> str | None:
    if name in _HIDDEN.get(component, ()):
        return None
    if prop.kind in ("children", "tabs", "child"):
        return None
    if prop.kind == "enum":
        shown = "|".join(prop.choices)
    elif prop.kind in ("int", "num") and prop.low is not None:
        shown = f"{prop.low:g}-{prop.high:g}" if prop.high is not None else "num"
    else:
        shown = {
            "text": "text",
            "literal": "string",
            "expr": "{expr}",
            "bool": "bool",
            "num": "num",
            "int": "int",
            "var": "state key",
            "name": "name",
            "src": "data:image/… or /path",
            "url": "https://…",
            "list": _NODE_ITEM_HINTS.get(
                (component, name), _ITEM_HINTS.get(name, "[…]")
            ),
            "map": "{…}",
            "setmap": "{key: expr}",
        }.get(prop.kind, prop.kind)
    star = "*" if prop.required else ""
    return f"{name}{star}: {shown}"


# Components whose props are loaded with ``info("ui:<name>")``; the rest are inline.
# ``EXAMPLES`` covers these and the inline components models most often get wrong.
ON_DEMAND = (
    "section",
    "accordion",
    "quote",
    "timeline",
    "progress",
    "tree",
    "image",
    "html",
    "math",
    "link",
    "icon",
    "gallery",
    "copy",
    "timer",
    "quiz",
    "date",
    "rating",
)

EXAMPLES = {
    "section": '<section title="Details">\n  <text>Background the reader may skip.</text>\n</section>',
    "accordion": '<accordion title="Why it works">\n  <text>Each step halves the range.</text>\n</accordion>',
    "quote": '<quote cite="PEP 20">Readability counts.</quote>',
    "timeline": '<timeline items={[{title: "v1.0", date: "2024-03", detail: "First release"}, {title: "v2.0", date: "2025-01", tone: "success"}]}/>',
    "progress": '<progress label="Migrated" value={34} max={50}/>\n<progress label="Coverage" value={0.82} max={1} style="ring" tone="success"/>',
    "tree": '<tree items={[{label: "src", open: true, children: ["main.py", {label: "utils", children: ["io.py"]}]}, "README.md"]}/>',
    "image": '<image src="/workspace/plot.png" alt="Loss curve" width={480}/>',
    "html": '<html title="Badge" height={120}>\n<style>.b{font:600 20px system-ui;padding:12px;border-radius:12px;background:linear-gradient(90deg,#8ec5fc,#e0c3fc)}</style>\n<div class="b">Level 3 unlocked</div>\n</html>',
    "math": "<math display>\\int_0^1 x^2\\,dx = \\tfrac{1}{3}</math>",
    "link": '<link href="https://docs.python.org/3/library/asyncio.html">asyncio docs</link>',
    "icon": '<stack direction="row" gap="sm"><icon name="check"/><text>Tests pass</text></stack>',
    "gallery": '<gallery columns={3} items={[{src: "/shots/login.png", caption: "Login"}, "/shots/home.png"]}/>',
    "compare": '<compare items={[{title: "SQLite", pros: ["No server"], cons: ["One writer"], verdict: "Good for one app"}, {title: "Postgres", pros: ["Concurrent writes"], cons: ["A server to run"]}]}/>',
    "copy": '<copy label="Install">pip install kohakuterrarium</copy>',
    "timer": '<timer mode="countdown" seconds={300} label="Break"/>',
    "quiz": '<quiz options={["O(n)", "O(log n)", "O(1)"]} answer="O(log n)" explain="Each step halves the range.">Time of a binary search?</quiz>',
    "date": '<state due="2026-11-01"/>\n<date label="Due" bind="due"/>\n<text>Due on {due}</text>',
    "rating": '<state score={0}/>\n<rating label="How useful?" bind="score" max={5}/>\n<button send="I rate it {score}/5">Send rating</button>',
}


def component_line(name: str) -> str:
    """One reference line for a component: its props, text child and children."""
    component = COMPONENTS[name]
    parts = [
        text
        for key, prop in component.props.items()
        if (text := _prop_text(name, key, prop)) is not None
    ]
    if name in ("button", "form"):
        parts.append("send: text sent as the user's message")
    target = TEXT_CHILD.get(name) or RAW_CHILD.get(name)
    kinds = {p.kind for p in component.props.values()}
    if target is not None:
        raw = " (raw)" if name in RAW_CHILD else ""
        parts.append(f"text child{raw} = {target}")
    elif kinds & {"children", "tabs"}:
        parts.append("tab children" if "tabs" in kinds else "children")
    elif "child" in kinds:
        parts.append("children repeat per item")
    return f"- `{name}`" + (": " + "; ".join(parts) if parts else "")


def component_lines(names: list[str] | None = None) -> list[str]:
    """Reference lines for ``names`` (every component by default), in spec order."""
    wanted = set(COMPONENTS) if names is None else set(names)
    return [component_line(name) for name in COMPONENTS if name in wanted]


def component_doc(name: str) -> str | None:
    """What ``info("ui:<name>")`` returns: the props line and an example."""
    if name not in COMPONENTS:
        return None
    parts = [
        f"# ui:{name}",
        "Props (* required; every component also takes `visible`):",
        component_line(name),
    ]
    if name in EXAMPLES:
        parts.append(f"Example:\n<ui>\n{EXAMPLES[name]}\n</ui>")
    return "\n\n".join(parts)


def inline_ui_section(on_demand: bool = True, html: bool = True) -> str:
    """The inline-components part: when to use them, syntax, components, example.

    With ``on_demand`` the ``ON_DEMAND`` components are named in one line and
    their props come from ``info``; without it every component is inline.
    ``html=False`` leaves out the ``html`` component, for clients that run no
    app frames.
    """
    shown = [n for n in COMPONENTS if html or n != "html"]
    inline = [n for n in shown if not (on_demand and n in ON_DEMAND)]
    components = (
        "#### Components (* required; every component also takes `visible`)\n"
        + "\n".join(component_lines(inline))
    )
    if on_demand:
        names = ", ".join(f"`{n}`" for n in shown if n in ON_DEMAND)
        components += (
            f"\n- More, ready to use: {names}. Before the first use of one in "
            "a conversation, call `info` with `ui:<name>` for its props and an "
            "example."
        )
    intro = _INTRO.replace(_MIX_LINE, (_HTML_LINE + "\n" if html else "") + _MIX_LINE)
    syntax = (
        _SYNTAX
        if html
        else _SYNTAX.replace("`math`, `copy` and `html`", "`math` and `copy`")
    )
    return "\n\n".join([intro, syntax, components, _EXPRESSIONS, _EXAMPLE])


__all__ = [
    "EXAMPLES",
    "ON_DEMAND",
    "component_doc",
    "component_line",
    "component_lines",
    "inline_ui_section",
]
