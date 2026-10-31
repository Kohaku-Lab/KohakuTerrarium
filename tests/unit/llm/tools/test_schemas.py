"""Builtin schema invariants and MCP direct-tool argument-name contracts.

The AST guard covers top-level literal reads and follows dictionary forwarding
into named Python helpers and bound methods. Unsupported dictionary uses fail.
Nested payloads, types, defaults and conditional requirements need behavior tests.
"""

import ast
import copy
import inspect
import textwrap

import pytest

from kohakuterrarium.api.mcp_tools import _tool_list
from kohakuterrarium.llm.tools.schemas import _BUILTIN_SCHEMAS
from kohakuterrarium.mcp_server.config import SUPPORTED_TOOLS, GlobalToolsConfig
from kohakuterrarium.mcp_server.runtime import ToolCatalog

# tree._body is the text-format path fallback, absent from native schemas.
_PRIVATE_ARGUMENTS = {"tree": {"_body"}}
# Executor._run_tool consumes bash.allow_concurrent before execution.
_FRAMEWORK_ARGUMENTS = {"bash": {"allow_concurrent"}}


def _argument_uses(source, argument):
    """Collect literal keys and raw-dictionary forwarding; reject other uses."""
    tree = ast.parse(textwrap.dedent(source))
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    keys = set()
    forwarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or node.id != argument:
            continue
        scope = parents[node]
        while scope is not tree.body[0]:
            assert not isinstance(
                scope,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Lambda,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            ), f"Nested argument scope needs explicit coverage: {ast.unparse(scope)}"
            scope = parents[scope]
        parent = parents[node]
        key = None
        if isinstance(parent, ast.Attribute) and parent.value is node:
            call = parents.get(parent)
            assert (
                parent.attr in {"get", "pop", "setdefault"}
                and isinstance(call, ast.Call)
                and call.func is parent
                and call.args
            ), f"Unsupported argument access: {ast.unparse(parent)}"
            key = call.args[0]
        elif isinstance(parent, ast.Subscript) and parent.value is node:
            assert isinstance(
                parent.ctx, ast.Load
            ), "Argument mutation needs explicit coverage"
            key = parent.slice
        elif (
            isinstance(parent, ast.Compare)
            and len(parent.ops) == 1
            and isinstance(parent.ops[0], (ast.In, ast.NotIn))
            and parent.comparators[0] is node
        ):
            key = parent.left
        elif isinstance(parent, ast.Call) and node in parent.args:
            forwarded.append((parent, node))
            continue
        elif isinstance(parent, ast.keyword) and parent.value is node and parent.arg:
            forwarded.append((parents[parent], node))
            continue
        else:
            raise AssertionError(f"Unsupported argument use: {ast.unparse(parent)}")
        assert isinstance(key, ast.Constant) and isinstance(
            key.value, str
        ), f"Dynamic argument key needs explicit coverage: {ast.unparse(key)}"
        keys.add(key.value)
    return keys, forwarded


def _consumed_arguments(function, argument="args", seen=None):
    """Follow named Python helpers and bound methods without executing tools."""
    seen = set() if seen is None else seen
    identity = (getattr(function, "__func__", function), argument)
    if identity in seen:
        return set()
    seen.add(identity)
    keys, forwarded = _argument_uses(inspect.getsource(function), argument)
    for call, node in forwarded:
        helper = None
        match call.func:
            case ast.Name(id=name):
                code = function.__code__
                assert (
                    name not in code.co_varnames + code.co_cellvars + code.co_freevars
                ), f"Shadowed helper needs explicit coverage: {ast.unparse(call)}"
                helper = function.__globals__.get(name)
            case ast.Attribute(value=ast.Name(id="self"), attr=name):
                helper = getattr(getattr(function, "__self__", None), name, None)
        assert inspect.isfunction(helper) or inspect.ismethod(
            helper
        ), f"Unresolved argument forwarding needs explicit coverage: {ast.unparse(call)}"
        assert not any(isinstance(value, ast.Starred) for value in call.args) and all(
            keyword.arg is not None for keyword in call.keywords
        ), f"Expanded helper arguments need explicit coverage: {ast.unparse(call)}"
        bindings = inspect.signature(helper).bind(
            *call.args, **{keyword.arg: keyword.value for keyword in call.keywords}
        )
        targets = [name for name, value in bindings.arguments.items() if value is node]
        assert (
            targets
        ), f"Variadic argument forwarding needs explicit coverage: {ast.unparse(call)}"
        for target in targets:
            keys.update(_consumed_arguments(helper, target, seen))
    return keys


def _assert_argument_contract(tool, schema):
    consumed = _consumed_arguments(tool._execute)
    private = _PRIVATE_ARGUMENTS.get(tool.tool_name, set())
    assert (
        private <= consumed
    ), f"Stale private argument exception: {private - consumed}"
    declared = set(schema["properties"])
    assert not private & declared, f"Internal arguments exposed: {private & declared}"
    framework = _FRAMEWORK_ARGUMENTS.get(tool.tool_name, set())
    if tool.supports_background:
        framework = framework | {"run_in_background"}
    assert framework <= declared, f"Missing framework arguments: {framework - declared}"
    public = consumed - private
    missing = public - declared
    unused = declared - public - framework
    assert not missing and not unused, (
        f"{tool.tool_name}: undeclared arguments={sorted(missing)}, "
        f"unconsumed schema properties={sorted(unused)}"
    )


@pytest.fixture
def tool_catalog(tmp_path):
    return ToolCatalog(GlobalToolsConfig(), tmp_path)


@pytest.mark.parametrize("name", SUPPORTED_TOOLS)
@pytest.mark.parametrize("surface", ["native", "mcp"])
def test_mcp_direct_tool_arguments_match_effective_schema(tool_catalog, name, surface):
    if surface == "native":
        schemas = {schema.name: schema.parameters for schema in tool_catalog.schemas()}
    else:
        schemas = {tool.name: tool.inputSchema for tool in _tool_list(tool_catalog)}
        assert schemas[name]["additionalProperties"] is False
    _assert_argument_contract(tool_catalog.registry.get_tool(name), schemas[name])


@pytest.mark.parametrize(
    "name,key",
    [
        ("glob", "limit"),
        ("grep", "limit"),
        ("grep", "ignore_case"),
        ("python", "timeout"),
        ("bash", "timeout"),
        ("edit", "replace_all"),
    ],
)
def test_contract_detects_missing_arguments_including_helpers(
    tool_catalog, monkeypatch, name, key
):
    schema = copy.deepcopy(_BUILTIN_SCHEMAS[name])
    del schema["properties"][key]
    monkeypatch.setitem(_BUILTIN_SCHEMAS, name, schema)
    schemas = {schema.name: schema.parameters for schema in tool_catalog.schemas()}
    with pytest.raises(AssertionError, match=f"undeclared arguments=.*{key}"):
        _assert_argument_contract(tool_catalog.registry.get_tool(name), schemas[name])


@pytest.mark.parametrize(
    "expression",
    [
        'args.get("limit", 50)',
        'args["limit"]',
        '"limit" in args',
        '"limit" not in args',
        'args.pop("limit", 50)',
        'args.setdefault("limit", 50)',
    ],
)
def test_argument_reader_recognizes_literal_keys(expression):
    keys, forwarded = _argument_uses(
        f"def execute(args):\n    return {expression}", "args"
    )
    assert keys == {"limit"}
    assert forwarded == []


@pytest.mark.parametrize(
    "statement",
    [
        "return args.get(key)",
        "return args[key]",
        "return key in args",
        "alias = args",
        "return handler(**args)",
        'args["new"] = 1',
        "return args.items()",
    ],
)
def test_argument_reader_rejects_unanalyzed_access(statement):
    source = f"def execute(args):\n    {statement}"
    with pytest.raises(AssertionError):
        _argument_uses(source, "args")


@pytest.mark.parametrize(
    "body",
    [
        'def unrelated(args):\n    return args.get("unused_option")\nunrelated({})',
        'unrelated = lambda args: args.get("unused_option")\nunrelated({})',
        '[args.get("unused_option") for args in [{}]]',
        'def closure():\n    return args.get("unused_option")\nclosure()',
    ],
)
def test_argument_reader_rejects_nested_argument_scopes(body):
    source = (
        "def execute(args):\n"
        + textwrap.indent(body, "    ")
        + '\n    return args.get("path")\n'
    )
    with pytest.raises(AssertionError, match="Nested argument scope"):
        _argument_uses(source, "args")


def _helper_argument_reads(options, unused=None):
    raise RuntimeError("The AST checker must not execute helpers")
    return options.get("helper_only")


def _keyword_forwarding(args):
    return _helper_argument_reads(options=args)


def _copied_arguments(args):
    return dict(args)


def _variadic_helper(*values):
    return values[0].get("invisible")


def _variadic_forwarding(args):
    return _variadic_helper(args)


def _expanded_forwarding(args):
    prefix = ()
    return _helper_argument_reads(*prefix, args)


def _different_argument_reads(options):
    return options.get("shadowed_key")


def _shadowed_forwarding(args):
    _helper_argument_reads = _different_argument_reads
    return _helper_argument_reads(args)


def test_argument_reader_rejects_positional_expansion():
    with pytest.raises(AssertionError, match="Expanded helper arguments"):
        _consumed_arguments(_expanded_forwarding)


def test_argument_reader_rejects_shadowed_helper():
    with pytest.raises(AssertionError, match="Shadowed helper"):
        _consumed_arguments(_shadowed_forwarding)


def test_argument_reader_follows_keyword_forwarding_without_execution():
    assert _consumed_arguments(_keyword_forwarding) == {"helper_only"}


@pytest.mark.parametrize("function", [_copied_arguments, _variadic_forwarding])
def test_argument_reader_rejects_unresolved_forwarding(function):
    with pytest.raises(AssertionError, match="forwarding needs explicit coverage"):
        _consumed_arguments(function)


@pytest.mark.parametrize(
    "name,key,message",
    [
        ("glob", "unused_option", "unconsumed schema properties=.*unused_option"),
        ("tree", "_body", "Internal arguments exposed"),
    ],
)
def test_contract_rejects_extra_or_internal_schema_arguments(
    tool_catalog, name, key, message
):
    schema = copy.deepcopy(
        next(
            schema.parameters
            for schema in tool_catalog.schemas()
            if schema.name == name
        )
    )
    schema["properties"][key] = {"type": "string"}
    with pytest.raises(AssertionError, match=message):
        _assert_argument_contract(tool_catalog.registry.get_tool(name), schema)


def _walk_properties(schema):
    """Yield every (name, prop_schema) recursively, including nested items."""
    props = schema.get("properties", {})
    for name, prop in props.items():
        yield name, prop
        if isinstance(prop, dict):
            items = prop.get("items")
            if isinstance(items, dict):
                yield from _walk_properties(items)
            if "properties" in prop:
                yield from _walk_properties(prop)


class TestBuiltinSchemas:
    def test_every_entry_is_an_object_schema(self):
        for name, schema in _BUILTIN_SCHEMAS.items():
            assert schema.get("type") == "object", f"{name} is not type=object"
            assert isinstance(
                schema.get("properties"), dict
            ), f"{name} missing properties dict"

    def test_required_names_are_declared_properties(self):
        for name, schema in _BUILTIN_SCHEMAS.items():
            declared = set(schema.get("properties", {}))
            for req in schema.get("required", []):
                assert (
                    req in declared
                ), f"{name}: required '{req}' is not a declared property"

    def test_every_enum_is_a_nonempty_list(self):
        for name, schema in _BUILTIN_SCHEMAS.items():
            for prop_name, prop in _walk_properties(schema):
                if isinstance(prop, dict) and "enum" in prop:
                    assert (
                        isinstance(prop["enum"], list) and prop["enum"]
                    ), f"{name}.{prop_name} has an empty/invalid enum"

    def test_known_core_tools_present(self):
        # build_tool_schemas relies on these being accurate, not the
        # generic {content: string} fallback
        for tool in ("bash", "read", "write", "edit", "grep", "glob"):
            assert tool in _BUILTIN_SCHEMAS

    def test_bash_command_is_required_string(self):
        bash = _BUILTIN_SCHEMAS["bash"]
        assert bash["required"] == ["command"]
        assert bash["properties"]["command"]["type"] == "string"

    def test_ask_user_schema_matches_runtime_args(self):
        # Parity guard: every optional arg AskUserTool._execute reads must
        # be a declared property so the provider doesn't strip it on the
        # wire. ``question`` stays the sole required field.
        schema = _BUILTIN_SCHEMAS["ask_user"]
        props = set(schema["properties"])
        assert {"question", "placeholder", "multiline", "timeout_s", "surface"} <= props
        assert schema["required"] == ["question"]

    def test_search_options_match_runtime_contract(self):
        glob = _BUILTIN_SCHEMAS["glob"]["properties"]
        grep = _BUILTIN_SCHEMAS["grep"]["properties"]
        assert glob["limit"]["type"] == "integer"
        assert grep["limit"]["type"] == "integer"
        assert grep["limit"]["minimum"] == 1
        assert grep["ignore_case"]["type"] == "boolean"

    def test_property_types_are_valid_json_schema_types(self):
        valid = {"string", "integer", "number", "boolean", "object", "array"}
        for name, schema in _BUILTIN_SCHEMAS.items():
            for prop_name, prop in _walk_properties(schema):
                if isinstance(prop, dict) and "type" in prop:
                    assert (
                        prop["type"] in valid
                    ), f"{name}.{prop_name} has invalid type '{prop['type']}'"
