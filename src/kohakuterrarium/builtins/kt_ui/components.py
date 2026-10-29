"""The ``kt.ui`` v1 component table: every node type and the kind of each prop.

The validator, the JSON schema and the text fallback all read this table, so
a component is defined once. Prop kinds:

``text``    string, may contain ``{{ expr }}``
``literal`` plain string, no templates
``expr``    a bare expression string (no braces)
``bool`` / ``num`` / ``int``  the value, or a ``{{ expr }}`` string
``enum``    one of the listed strings
``var``     name of a ``state`` entry (``bind`` targets)
``name``    a message name (``send``)
``src``     image source: ``data:image/...`` or a same-origin path
``list``    a JSON array, or a ``{{ expr }}`` string that yields one
``map``     a JSON object, or a ``{{ expr }}`` string that yields one
``setmap``  object of state name -> bare expression
``children`` / ``child`` / ``tabs``  nested nodes
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Prop:
    """One prop of a component."""

    kind: str
    required: bool = False
    choices: tuple[str, ...] = ()
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class Component:
    """A node type: its props and whether it is an input or action."""

    props: dict[str, Prop] = field(default_factory=dict)
    group: str = "content"


_GAP = ("none", "sm", "md", "lg")
_TONE = ("neutral", "info", "success", "warning", "danger")
_INPUT = {"bind": Prop("var", required=True), "label": Prop("text")}

COMMON_PROPS: dict[str, Prop] = {
    "type": Prop("literal", required=True),
    "visible": Prop("bool"),
    "alt": Prop("text"),
}

COMPONENTS: dict[str, Component] = {
    # layout
    "stack": Component(
        {
            "direction": Prop("enum", choices=("column", "row")),
            "gap": Prop("enum", choices=_GAP),
            "wrap": Prop("bool"),
            "align": Prop("enum", choices=("start", "center", "end", "stretch")),
            "children": Prop("children", required=True),
        },
        "layout",
    ),
    "grid": Component(
        {
            "columns": Prop("int", low=1, high=12),
            "gap": Prop("enum", choices=_GAP),
            "children": Prop("children", required=True),
        },
        "layout",
    ),
    "card": Component(
        {
            "title": Prop("text"),
            "subtitle": Prop("text"),
            "children": Prop("children", required=True),
        },
        "layout",
    ),
    "section": Component(
        {"title": Prop("text"), "children": Prop("children", required=True)},
        "layout",
    ),
    "tabs": Component({"children": Prop("tabs", required=True)}, "layout"),
    "tab": Component(
        {"label": Prop("text", required=True), "children": Prop("children")},
        "layout",
    ),
    "divider": Component({}, "layout"),
    # content
    "text": Component(
        {
            "value": Prop("text", required=True),
            "size": Prop("enum", choices=("sm", "md", "lg")),
            "muted": Prop("bool"),
        }
    ),
    "heading": Component(
        {"value": Prop("text", required=True), "level": Prop("int", low=1, high=4)}
    ),
    "badge": Component(
        {"value": Prop("text", required=True), "tone": Prop("enum", choices=_TONE)}
    ),
    "stat": Component(
        {
            "label": Prop("text"),
            "value": Prop("text", required=True),
            "hint": Prop("text"),
            "tone": Prop("enum", choices=_TONE),
        }
    ),
    "kv": Component({"fields": Prop("list", required=True)}),
    "list": Component({"items": Prop("list", required=True), "ordered": Prop("bool")}),
    "table": Component(
        {"columns": Prop("list", required=True), "rows": Prop("list", required=True)}
    ),
    "image": Component(
        {
            "src": Prop("src", required=True),
            "alt": Prop("text"),
            "width": Prop("int", low=16, high=2048),
        }
    ),
    "code": Component({"value": Prop("text", required=True), "lang": Prop("literal")}),
    # data
    "chart": Component(
        {
            "kind": Prop(
                "enum",
                required=True,
                choices=("bar", "line", "area", "pie", "scatter"),
            ),
            "labels": Prop("list"),
            "series": Prop("list", required=True),
            "title": Prop("text"),
            "height": Prop("int", low=80, high=800),
        },
        "data",
    ),
    "diagram": Component(
        {"source": Prop("text", required=True), "title": Prop("text")}, "data"
    ),
    # inputs
    "number": Component(
        {**_INPUT, "min": Prop("num"), "max": Prop("num"), "step": Prop("num")},
        "input",
    ),
    "slider": Component(
        {
            **_INPUT,
            "min": Prop("num", required=True),
            "max": Prop("num", required=True),
            "step": Prop("num"),
        },
        "input",
    ),
    "text_input": Component(
        {**_INPUT, "placeholder": Prop("text"), "multiline": Prop("bool")}, "input"
    ),
    "select": Component({**_INPUT, "options": Prop("list", required=True)}, "input"),
    "radio": Component({**_INPUT, "options": Prop("list", required=True)}, "input"),
    "toggle": Component(dict(_INPUT), "input"),
    "checkbox": Component(dict(_INPUT), "input"),
    "date": Component(dict(_INPUT), "input"),
    # actions
    "button": Component(
        {
            "label": Prop("text", required=True),
            "style": Prop("enum", choices=("primary", "secondary", "danger")),
            "set": Prop("setmap"),
            "send": Prop("name"),
            "data": Prop("map"),
            "disabled": Prop("bool"),
        },
        "action",
    ),
    "form": Component(
        {
            "children": Prop("children", required=True),
            "send": Prop("name", required=True),
            "submit": Prop("text"),
            "data": Prop("map"),
        },
        "action",
    ),
    # repeat
    "each": Component(
        {
            "items": Prop("expr", required=True),
            "as": Prop("literal"),
            "child": Prop("child", required=True),
        },
        "repeat",
    ),
}

INPUT_TYPES = frozenset(t for t, c in COMPONENTS.items() if c.group == "input")

__all__ = ["COMMON_PROPS", "COMPONENTS", "Component", "INPUT_TYPES", "Prop"]
