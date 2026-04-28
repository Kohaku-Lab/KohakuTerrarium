"""Codegen for sub-agent modules (Python form).

Pattern of a sub-agent module:

    SYSTEM_PROMPT = \"\"\"...\"\"\"

    EXPLORE_CONFIG = SubAgentConfig(
        name="explore",
        description="...",
        tools=[...],
        system_prompt=SYSTEM_PROMPT,
        can_modify=False,
        stateless=True,
    )

Round-trip strategy: the config is a single ``Call`` expression
with keyword arguments. We parse the kwargs into form state and
rewrite them on save. The optional ``SYSTEM_PROMPT`` string is
kept next to the call.
"""

import libcst as cst

from kohakuterrarium.studio.editors.codegen_common import RoundTripError, parse
from kohakuterrarium.studio.editors.templates import render

_FORM_FIELDS = (
    "name",
    "description",
    "tools",
    "system_prompt",
    "can_modify",
    "stateless",
    "interactive",
)


def render_new(form: dict) -> str:
    """Scaffold a new sub-agent module."""
    name = form.get("name", "my_subagent")
    config_var = f"{name.upper()}_CONFIG"
    system_prompt_var = "SYSTEM_PROMPT"
    return render(
        "subagent.py.j2",
        name=name,
        description=form.get("description", f"TODO: describe the {name} sub-agent"),
        tools=form.get("tools") or [],
        system_prompt=form.get("system_prompt") or f"You are the {name} sub-agent.",
        config_var=config_var,
        system_prompt_var=system_prompt_var,
        can_modify=bool(form.get("can_modify", False)),
        stateless=bool(form.get("stateless", True)),
        interactive=bool(form.get("interactive", False)),
    )


def update_existing(source: str, form: dict, execute_body: str) -> str:
    """Rewrite the SubAgentConfig call's keyword arguments.

    *execute_body* unused (sub-agents don't have one).
    """
    del execute_body

    tree = parse(source)

    call_path = _find_subagent_config_call(tree)
    if call_path is None:
        raise RoundTripError("no module-level SubAgentConfig(...) call found")

    new_call = _rewrite_call_kwargs(call_path, form)

    class _Replacer(cst.CSTTransformer):
        def leave_Call(self, orig, updated):
            if orig is call_path:
                return new_call
            return updated

    return tree.visit(_Replacer()).code


def parse_back(source: str) -> dict:
    """Extract the sub-agent form state."""
    try:
        tree = parse(source)
    except Exception as e:
        return _raw_envelope(f"parse failed: {e}")

    call = _find_subagent_config_call(tree)
    if call is None:
        return _raw_envelope("no SubAgentConfig(...) call found")

    form: dict = {
        "name": "",
        "description": "",
        "tools": [],
        "system_prompt": "",
        "can_modify": False,
        "stateless": True,
        "interactive": False,
    }

    # Also resolve SYSTEM_PROMPT = "..." if the call references a name
    sys_prompts = _collect_string_assignments(tree)

    for arg in call.args:
        if arg.keyword is None:
            continue
        key = arg.keyword.value
        if key not in _FORM_FIELDS:
            continue
        form[key] = _eval_simple(arg.value, sys_prompts)

    return {
        "mode": "simple",
        "form": form,
        "execute_body": "",
        "warnings": [],
    }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _find_subagent_config_call(tree: cst.Module) -> cst.Call | None:
    """Find the first module-level ``SubAgentConfig(...)`` call."""
    for node in tree.body:
        if not isinstance(node, cst.SimpleStatementLine):
            continue
        for stmt in node.body:
            if isinstance(stmt, cst.Assign):
                value = stmt.value
                if isinstance(value, cst.Call):
                    target = value.func
                    if (
                        isinstance(target, cst.Name)
                        and target.value == "SubAgentConfig"
                    ):
                        return value
                    if (
                        isinstance(target, cst.Attribute)
                        and isinstance(target.attr, cst.Name)
                        and target.attr.value == "SubAgentConfig"
                    ):
                        return value
    return None


def _rewrite_call_kwargs(call: cst.Call, form: dict) -> cst.Call:
    """Build a new Call with updated kwargs for the form fields."""
    # Preserve args we don't manage; rewrite/add ones we do.
    new_args: list[cst.Arg] = []
    consumed: set[str] = set()

    for arg in call.args:
        if arg.keyword is None:
            new_args.append(arg)
            continue
        key = arg.keyword.value
        if key in _FORM_FIELDS and key in form:
            value_node = _literal_to_cst(form[key])
            new_args.append(arg.with_changes(value=value_node))
            consumed.add(key)
        else:
            new_args.append(arg)

    # Add any form fields that weren't in the original args
    for key in _FORM_FIELDS:
        if key in form and key not in consumed:
            value_node = _literal_to_cst(form[key])
            new_args.append(
                cst.Arg(
                    keyword=cst.Name(key),
                    value=value_node,
                    equal=cst.AssignEqual(
                        whitespace_before=cst.SimpleWhitespace(""),
                        whitespace_after=cst.SimpleWhitespace(""),
                    ),
                )
            )
    return call.with_changes(args=new_args)


def _literal_to_cst(value) -> cst.BaseExpression:
    """Turn a Python value into a libcst node."""
    if isinstance(value, bool):
        return cst.Name(value="True" if value else "False")
    if isinstance(value, int):
        return cst.Integer(value=str(value))
    if isinstance(value, float):
        return cst.Float(value=repr(value))
    if isinstance(value, str):
        if "\n" in value:
            escaped = value.replace('"""', r"\"\"\"")
            return cst.SimpleString(value=f'"""{escaped}"""')
        return cst.SimpleString(value=repr(value))
    if isinstance(value, list):
        return cst.List(elements=[cst.Element(value=_literal_to_cst(v)) for v in value])
    if value is None:
        return cst.Name(value="None")
    raise ValueError(f"cannot serialize {type(value).__name__}")


def _eval_simple(node: cst.BaseExpression, string_bindings: dict[str, str]):
    """Interpret a small subset of expressions as Python literals."""
    if isinstance(node, cst.SimpleString):
        try:
            return node.evaluated_value
        except Exception:
            return node.value
    if isinstance(node, cst.ConcatenatedString):
        # Best-effort concatenation of adjacent string literals
        try:
            return node.evaluated_value
        except Exception:
            return ""
    if isinstance(node, cst.Name):
        if node.value == "True":
            return True
        if node.value == "False":
            return False
        if node.value == "None":
            return None
        # Named reference (e.g. SYSTEM_PROMPT)
        return string_bindings.get(node.value, "")
    if isinstance(node, cst.Integer):
        try:
            return int(node.value)
        except ValueError:
            return 0
    if isinstance(node, cst.List):
        return [_eval_simple(e.value, string_bindings) for e in node.elements]
    if isinstance(node, cst.Tuple):
        return [_eval_simple(e.value, string_bindings) for e in node.elements]
    return None


def _collect_string_assignments(tree: cst.Module) -> dict[str, str]:
    """Return {NAME: value} for module-level ``NAME = "..."`` assignments."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, cst.SimpleStatementLine):
            continue
        for stmt in node.body:
            if not isinstance(stmt, cst.Assign) or len(stmt.targets) != 1:
                continue
            tgt = stmt.targets[0].target
            if not isinstance(tgt, cst.Name):
                continue
            value = stmt.value
            if isinstance(value, cst.SimpleString):
                try:
                    out[tgt.value] = value.evaluated_value
                except Exception:
                    pass
            elif isinstance(value, cst.ConcatenatedString):
                try:
                    out[tgt.value] = value.evaluated_value
                except Exception:
                    pass
    return out


def _raw_envelope(reason: str) -> dict:
    return {
        "mode": "raw",
        "form": {
            "name": "",
            "description": "",
            "tools": [],
            "system_prompt": "",
            "can_modify": False,
            "stateless": True,
            "interactive": False,
        },
        "execute_body": "",
        "warnings": [{"code": "ast_roundtrip_unsafe", "message": reason}],
    }
