"""Validate a ``kt.ui`` spec and build the scope its expressions run in.

:func:`validate_spec` checks a spec against the component table and returns a
:class:`ValidatedSpec`, or raises :class:`SpecError` listing every problem with
its JSON path, so a model can fix the spec in one more call.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.builtins.kt_ui.components import COMMON_PROPS, COMPONENTS, Prop
from kohakuterrarium.builtins.kt_ui.expr import (
    FUNCTIONS,
    KEYWORDS,
    ExprError,
    evaluate,
    free_names,
    parse,
    template_expression,
    template_expressions,
)
from kohakuterrarium.builtins.kt_ui.shapes import (
    ITEM_SHAPES,
    NODE_CHECKS,
    NODE_ITEM_SHAPES,
    NODE_LIST_CHECKS,
    is_outward_url,
    is_safe_src,
    state_name_problem,
)
from kohakuterrarium.modules.output.widgets import MESSAGE_NAME

SPEC_VERSION = 1
MAX_SPEC_BYTES = 64 * 1024
MAX_NODES = 2000
MAX_DEPTH = 24
MAX_ERRORS = 20
TOP_LEVEL_KEYS = frozenset({"version", "title", "layout", "state", "computed", "root"})
LAYOUTS = ("inline", "page")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class SpecError(ValueError):
    """A spec failed validation; ``errors`` holds ``(path, message)`` pairs."""

    def __init__(self, errors: list[tuple[str, str]]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{path}: {msg}" for path, msg in errors))


@dataclass
class ValidatedSpec:
    """A spec that passed validation, with what the tool needs from it."""

    spec: dict[str, Any]
    send_names: set[str] = field(default_factory=set)
    computed_order: list[str] = field(default_factory=list)

    @property
    def interactive(self) -> bool:
        return bool(self.send_names)

    @property
    def layout(self) -> str:
        return self.spec.get("layout", "inline")


class _Checker:
    def __init__(self, state_keys: set[str], computed_keys: set[str]) -> None:
        self.errors: list[tuple[str, str]] = []
        self.state_keys = state_keys
        self.base_scope = state_keys | computed_keys
        self.send_names: set[str] = set()
        self.nodes = 0

    def error(self, path: str, message: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append((path, message))

    def expression(self, path: str, text: str, scope: set[str]) -> None:
        try:
            names = free_names(parse(text))
        except ExprError as exc:
            self.error(path, f"bad expression {text!r}: {exc}")
            return
        for name in sorted(names - scope):
            self.error(path, f"unknown name {name!r} in {text!r}")

    def templates(self, path: str, value: Any, scope: set[str]) -> None:
        """Check every ``{{ expr }}`` inside a JSON value."""
        if isinstance(value, str):
            for inner in template_expressions(value):
                self.expression(path, inner, scope)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                self.templates(f"{path}[{i}]", item, scope)
        elif isinstance(value, dict):
            for key, item in value.items():
                self.templates(f"{path}.{key}", item, scope)

    def dynamic(self, path: str, value: Any, scope: set[str]) -> bool:
        """True (after checking it) when ``value`` is a single ``{{ expr }}``."""
        if isinstance(value, str) and template_expression(value) is not None:
            self.expression(path, template_expression(value), scope)
            return True
        return False

    def prop(
        self, path: str, node_type: str, name: str, spec: Prop, value: Any, scope: set
    ) -> None:
        match spec.kind:
            case "text":
                if not isinstance(value, str):
                    self.error(path, "must be a string")
                else:
                    self.templates(path, value, scope)
            case "literal":
                if not isinstance(value, str):
                    self.error(path, "must be a string")
            case "expr":
                if not isinstance(value, str):
                    self.error(path, "must be an expression string")
                else:
                    self.expression(path, value, scope)
            case "bool":
                if not isinstance(value, bool) and not self.dynamic(path, value, scope):
                    self.error(path, "must be true, false or '{{ expr }}'")
            case "num" | "int":
                self._number(path, spec, value, scope)
            case "enum":
                if value not in spec.choices:
                    self.error(path, f"must be one of {', '.join(spec.choices)}")
            case "var":
                if not isinstance(value, str) or value not in self.state_keys:
                    self.error(path, f"{value!r} is not a key of 'state'")
            case "name":
                if not isinstance(value, str) or not MESSAGE_NAME.fullmatch(value):
                    self.error(path, "must be a message name ([A-Za-z0-9_.:-], 1-64)")
                else:
                    self.send_names.add(value)
            case "src":
                self._src(path, value, scope)
            case "url":
                self._url(path, value, scope)
            case "list" | "map":
                self._container(path, node_type, name, spec.kind, value, scope)
            case "setmap":
                self._setmap(path, value, scope)

    def _number(self, path: str, spec: Prop, value: Any, scope: set) -> None:
        if self.dynamic(path, value, scope):
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.error(path, "must be a number or '{{ expr }}'")
            return
        if spec.kind == "int" and value != int(value):
            self.error(path, "must be a whole number")
        if spec.low is not None and value < spec.low:
            self.error(path, f"must be >= {spec.low:g}")
        if spec.high is not None and value > spec.high:
            self.error(path, f"must be <= {spec.high:g}")

    def _src(self, path: str, value: Any, scope: set) -> None:
        if self.dynamic(path, value, scope):
            return
        if not is_safe_src(value):
            self.error(path, "must be a data:image/ URL or a same-origin path")

    def _url(self, path: str, value: Any, scope: set) -> None:
        if self.dynamic(path, value, scope):
            return
        if not isinstance(value, str):
            self.error(path, "must be a string")
            return
        self.templates(path, value, scope)
        fixed = value.split("{{", 1)[0]
        ok = (
            is_outward_url(value)
            if fixed == value
            else fixed.lower().startswith(("http://", "https://"))
        )
        if not ok:
            self.error(path, "must be an http:// or https:// link")

    def _container(
        self, path: str, node_type: str, name: str, kind: str, value: Any, scope: set
    ) -> None:
        if self.dynamic(path, value, scope):
            return
        expected = list if kind == "list" else dict
        if not isinstance(value, expected):
            label = "an array" if kind == "list" else "an object"
            self.error(path, f"must be {label} or '{{{{ expr }}}}'")
            return
        self.templates(path, value, scope)
        whole = NODE_LIST_CHECKS.get((node_type, name))
        problem = whole(value) if kind == "list" and whole is not None else None
        if problem:
            self.error(path, problem)
            return
        shape = NODE_ITEM_SHAPES.get((node_type, name), ITEM_SHAPES.get(name))
        if kind == "list" and shape is not None:
            for i, item in enumerate(value):
                problem = shape(item)
                if problem:
                    self.error(f"{path}[{i}]", problem)

    def _setmap(self, path: str, value: Any, scope: set) -> None:
        if not isinstance(value, dict):
            self.error(path, "must map state keys to expressions")
            return
        for key, expr in value.items():
            if key not in self.state_keys:
                self.error(f"{path}.{key}", f"{key!r} is not a key of 'state'")
            if not isinstance(expr, str):
                self.error(f"{path}.{key}", "must be an expression string")
            else:
                self.expression(f"{path}.{key}", expr, scope)

    def node(self, path: str, node: Any, scope: set[str], depth: int) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            if self.nodes == MAX_NODES + 1:
                self.error(path, f"more than {MAX_NODES} nodes")
            return
        if depth > MAX_DEPTH:
            self.error(path, f"nested deeper than {MAX_DEPTH}")
            return
        if not isinstance(node, dict):
            self.error(path, "must be a component object with a 'type'")
            return
        node_type = node.get("type")
        component = COMPONENTS.get(node_type) if isinstance(node_type, str) else None
        if component is None:
            self.error(f"{path}.type", f"unknown component {node_type!r}")
            return
        props = {**COMMON_PROPS, **component.props}
        for key in node:
            if key not in props:
                self.error(f"{path}.{key}", f"unknown prop for {node_type}")
        check = NODE_CHECKS.get(node_type)
        for key, problem in check(node) if check is not None else ():
            self.error(f"{path}.{key}", problem)
        for key, spec in props.items():
            if key not in node:
                if spec.required:
                    self.error(f"{path}.{key}", "is required")
                continue
            if key == "type":
                continue
            value = node[key]
            child_scope = scope
            if node_type == "each" and key == "child":
                child_scope = scope | {self._each_name(path, node), "index"}
            match spec.kind:
                case "children" | "tabs":
                    self._children(f"{path}.{key}", value, child_scope, depth, spec)
                case "child":
                    self.node(f"{path}.{key}", value, child_scope, depth + 1)
                case _:
                    self.prop(f"{path}.{key}", node_type, key, spec, value, scope)

    def _children(
        self, path: str, value: Any, scope: set[str], depth: int, spec: Prop
    ) -> None:
        if not isinstance(value, list):
            self.error(path, "must be an array of components")
            return
        for i, child in enumerate(value):
            if spec.kind == "tabs" and (
                not isinstance(child, dict) or child.get("type") != "tab"
            ):
                self.error(f"{path}[{i}]", "tabs may only contain 'tab' nodes")
                continue
            self.node(f"{path}[{i}]", child, scope, depth + 1)

    def _each_name(self, path: str, node: dict) -> str:
        name = node.get("as", "item")
        if not _valid_name(name):
            self.error(f"{path}.as", "must be an identifier")
            return "item"
        return name


def _valid_name(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(_IDENT.fullmatch(name))
        and name not in KEYWORDS
        and name not in FUNCTIONS
    )


def _computed_order(
    computed: dict[str, str], errors: list[tuple[str, str]]
) -> list[str]:
    deps: dict[str, set[str]] = {}
    for name, expr in computed.items():
        try:
            deps[name] = free_names(parse(expr)) & set(computed)
        except ExprError:
            deps[name] = set()
    order: list[str] = []
    marks: dict[str, str] = {}

    def visit(name: str, trail: list[str]) -> None:
        if marks.get(name) == "done":
            return
        if marks.get(name) == "active":
            cycle = " -> ".join(trail[trail.index(name) :] + [name])
            errors.append((f"computed.{name}", f"dependency cycle: {cycle}"))
            return
        marks[name] = "active"
        for dep in sorted(deps[name]):
            visit(dep, trail + [name])
        marks[name] = "done"
        order.append(name)

    for name in computed:
        visit(name, [])
    return order


def validate_spec(spec: Any) -> ValidatedSpec:
    """Validate ``spec``; raise :class:`SpecError` with every problem found."""
    errors: list[tuple[str, str]] = []
    if not isinstance(spec, dict):
        raise SpecError([("spec", "must be an object")])
    size = len(json.dumps(spec, ensure_ascii=False, default=str).encode("utf-8"))
    if size > MAX_SPEC_BYTES:
        raise SpecError([("spec", f"is {size} bytes; the limit is {MAX_SPEC_BYTES}")])
    for key in spec:
        if key not in TOP_LEVEL_KEYS:
            errors.append((key, "unknown top-level key"))
    if spec.get("version", SPEC_VERSION) != SPEC_VERSION:
        errors.append(("version", f"must be {SPEC_VERSION}"))
    if spec.get("layout", "inline") not in LAYOUTS:
        errors.append(("layout", "must be 'inline' or 'page'"))
    if not isinstance(spec.get("title", ""), str):
        errors.append(("title", "must be a string"))

    state = spec.get("state", {})
    if not isinstance(state, dict):
        errors.append(("state", "must be an object"))
        state = {}
    computed = spec.get("computed", {})
    if not isinstance(computed, dict):
        errors.append(("computed", "must be an object"))
        computed = {}
    for key in state:
        if not _valid_name(key):
            errors.append((f"state.{key}", "must be an identifier, not a keyword"))
        elif reserved := state_name_problem(key):
            errors.append((f"state.{key}", reserved))
    for key, expr in computed.items():
        if not _valid_name(key):
            errors.append((f"computed.{key}", "must be an identifier"))
        elif key in state:
            errors.append((f"computed.{key}", "is already a key of 'state'"))
        if not isinstance(expr, str):
            errors.append((f"computed.{key}", "must be an expression string"))

    computed = {k: v for k, v in computed.items() if isinstance(v, str)}
    checker = _Checker(set(state), set(computed))
    for key, expr in computed.items():
        checker.expression(f"computed.{key}", expr, checker.base_scope)
    order = _computed_order(computed, checker.errors)
    if "root" not in spec:
        checker.error("root", "is required")
    else:
        checker.node("root", spec["root"], checker.base_scope, 0)
    if isinstance(spec.get("title"), str):
        checker.templates("title", spec["title"], checker.base_scope)

    errors.extend(checker.errors)
    if errors:
        raise SpecError(errors[:MAX_ERRORS])
    clean = {"version": SPEC_VERSION, "layout": "inline", **spec}
    return ValidatedSpec(
        spec=clean, send_names=checker.send_names, computed_order=order
    )


def build_scope(
    validated: ValidatedSpec, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """State merged with every computed value, in dependency order (errors give null)."""
    scope: dict[str, Any] = {
        **(validated.spec.get("state") or {}),
        **(state or {}),
    }
    computed = validated.spec.get("computed") or {}
    for name in validated.computed_order:
        try:
            scope[name] = evaluate(computed[name], scope)
        except ExprError:
            scope[name] = None
    return scope


def _prop_schema(prop: Prop) -> dict[str, Any]:
    template = {"type": "string", "pattern": r"^\s*\{\{.*\}\}\s*$"}
    match prop.kind:
        case "text" | "literal" | "expr" | "var" | "src" | "url":
            return {"type": "string"}
        case "name":
            return {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,64}$"}
        case "bool":
            return {"anyOf": [{"type": "boolean"}, template]}
        case "num" | "int":
            number: dict[str, Any] = {
                "type": "integer" if prop.kind == "int" else "number"
            }
            if prop.low is not None:
                number["minimum"] = prop.low
            if prop.high is not None:
                number["maximum"] = prop.high
            return {"anyOf": [number, template]}
        case "enum":
            return {"enum": list(prop.choices)}
        case "list":
            return {"anyOf": [{"type": "array"}, template]}
        case "map":
            return {"anyOf": [{"type": "object"}, template]}
        case "setmap":
            return {"type": "object", "additionalProperties": {"type": "string"}}
        case "children" | "tabs":
            return {"type": "array", "items": {"$ref": "#/$defs/node"}}
        case "child":
            return {"$ref": "#/$defs/node"}
    return {}


def json_schema() -> dict[str, Any]:
    """The JSON schema of a v1 spec, generated from the component table."""
    variants = []
    for node_type, component in COMPONENTS.items():
        props = {**COMMON_PROPS, **component.props}
        properties = {key: _prop_schema(spec) for key, spec in props.items()}
        properties["type"] = {"const": node_type}
        variants.append(
            {
                "type": "object",
                "properties": properties,
                "required": [k for k, s in props.items() if s.required],
                "additionalProperties": False,
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "kt.ui spec v1",
        "type": "object",
        "properties": {
            "version": {"const": SPEC_VERSION},
            "title": {"type": "string"},
            "layout": {"enum": list(LAYOUTS)},
            "state": {"type": "object"},
            "computed": {"type": "object", "additionalProperties": {"type": "string"}},
            "root": {"$ref": "#/$defs/node"},
        },
        "required": ["root"],
        "additionalProperties": False,
        "$defs": {"node": {"oneOf": variants}},
    }


__all__ = [
    "LAYOUTS",
    "MAX_SPEC_BYTES",
    "SPEC_VERSION",
    "SpecError",
    "ValidatedSpec",
    "build_scope",
    "json_schema",
    "validate_spec",
]
