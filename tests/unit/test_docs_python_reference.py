"""Drift guard for ``docs/en/reference/python.md``.

The reference documents the public Python surface by name and
signature.  This suite pins every documented symbol so the doc cannot
silently rot:

- every ``(module, attribute)`` pair below must import / resolve,
- every documented parameter name must exist on the real signature
  (via ``inspect.signature``),
- documented dataclass fields, enum members, and error base classes
  must exist,
- every pinned symbol's name must still appear in the reference doc
  (so the doc and this list move together).

If someone renames or removes a documented symbol — or rewrites the
doc to drop one that is still pinned here — this test fails.  Update
``docs/en/reference/python.md`` and this list in the same change.
"""

import dataclasses
import importlib
import importlib.util
import inspect
from pathlib import Path

import pytest

DOC_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "en" / "reference" / "python.md"
)


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _resolve(module: str, attr: str | None):
    obj = importlib.import_module(module)
    if attr:
        for part in attr.split("."):
            obj = getattr(obj, part)
    return obj


# ---------------------------------------------------------------------------
# Every public symbol the reference documents: (module, attr_path).
# ---------------------------------------------------------------------------

DOCUMENTED_SYMBOLS: list[tuple[str, str]] = [
    # -- package-root re-exports -------------------------------------
    ("kohakuterrarium", "Agent"),
    ("kohakuterrarium", "Terrarium"),
    ("kohakuterrarium", "Creature"),
    ("kohakuterrarium", "Studio"),
    ("kohakuterrarium", "tool"),
    ("kohakuterrarium", "FunctionTool"),
    ("kohakuterrarium", "SessionReader"),
    ("kohakuterrarium", "SessionStore"),
    ("kohakuterrarium", "TurnResult"),
    ("kohakuterrarium", "TextChunk"),
    ("kohakuterrarium", "Activity"),
    ("kohakuterrarium", "TurnEnded"),
    ("kohakuterrarium", "EngineEvent"),
    ("kohakuterrarium", "EventKind"),
    ("kohakuterrarium", "EventFilter"),
    ("kohakuterrarium", "ConnectionResult"),
    ("kohakuterrarium", "DisconnectionResult"),
    ("kohakuterrarium", "errors"),
    ("kohakuterrarium", "validate"),
    # -- errors --------------------------------------------------------
    ("kohakuterrarium.errors", "KTError"),
    ("kohakuterrarium.errors", "ConfigError"),
    ("kohakuterrarium.errors", "ConfigNotFoundError"),
    ("kohakuterrarium.errors", "PackageError"),
    ("kohakuterrarium.errors", "PackageRefError"),
    ("kohakuterrarium.errors", "PackageNotInstalledError"),
    ("kohakuterrarium.errors", "PackagePathNotFoundError"),
    ("kohakuterrarium.errors", "LLMError"),
    ("kohakuterrarium.errors", "LLMNotConfiguredError"),
    ("kohakuterrarium.errors", "SessionError"),
    ("kohakuterrarium.errors", "SessionNotResumableError"),
    ("kohakuterrarium.errors", "SessionNotFoundError"),
    ("kohakuterrarium.errors", "TurnError"),
    ("kohakuterrarium.errors", "TurnTimeoutError"),
    ("kohakuterrarium.errors", "AgentNotRunningError"),
    ("kohakuterrarium.errors", "NotFoundError"),
    ("kohakuterrarium.errors", "InvalidRequestError"),
    ("kohakuterrarium.errors", "ConflictError"),
    # -- Agent -----------------------------------------------------------
    ("kohakuterrarium", "Agent.build"),
    ("kohakuterrarium", "Agent.from_path"),
    ("kohakuterrarium", "Agent.run"),
    ("kohakuterrarium", "Agent.run_stream"),
    ("kohakuterrarium", "Agent.run_forever"),
    ("kohakuterrarium", "Agent.start"),
    ("kohakuterrarium", "Agent.stop"),
    ("kohakuterrarium", "Agent.interrupt"),
    ("kohakuterrarium", "Agent.inject_input"),
    ("kohakuterrarium", "Agent.add_tool"),
    ("kohakuterrarium", "Agent.add_plugin"),
    ("kohakuterrarium", "Agent.add_subagent"),
    ("kohakuterrarium", "Agent.refresh_system_prompt"),
    ("kohakuterrarium", "Agent.switch_model"),
    ("kohakuterrarium", "Agent.llm_identifier"),
    ("kohakuterrarium", "Agent.attach_session_store"),
    ("kohakuterrarium", "Agent.set_output_handler"),
    # -- turn surface ------------------------------------------------------
    ("kohakuterrarium.core.turn", "AgentEventStream"),
    ("kohakuterrarium.core.turn", "TurnResult.ok"),
    # -- Terrarium engine -------------------------------------------------
    ("kohakuterrarium", "Terrarium.from_recipe"),
    ("kohakuterrarium", "Terrarium.resume"),
    ("kohakuterrarium", "Terrarium.with_creature"),
    ("kohakuterrarium", "Terrarium.adopt_session"),
    ("kohakuterrarium", "Terrarium.add_creature"),
    ("kohakuterrarium", "Terrarium.remove_creature"),
    ("kohakuterrarium", "Terrarium.get_creature"),
    ("kohakuterrarium", "Terrarium.list_creatures"),
    ("kohakuterrarium", "Terrarium.add_channel"),
    ("kohakuterrarium", "Terrarium.remove_channel"),
    ("kohakuterrarium", "Terrarium.connect"),
    ("kohakuterrarium", "Terrarium.disconnect"),
    ("kohakuterrarium", "Terrarium.environment"),
    ("kohakuterrarium", "Terrarium.channel"),
    ("kohakuterrarium", "Terrarium.get_graph"),
    ("kohakuterrarium", "Terrarium.list_graphs"),
    ("kohakuterrarium", "Terrarium.apply_recipe"),
    ("kohakuterrarium", "Terrarium.attach_session"),
    ("kohakuterrarium", "Terrarium.assign_root"),
    ("kohakuterrarium", "Terrarium.start"),
    ("kohakuterrarium", "Terrarium.stop"),
    ("kohakuterrarium", "Terrarium.stop_graph"),
    ("kohakuterrarium", "Terrarium.shutdown"),
    ("kohakuterrarium", "Terrarium.subscribe"),
    ("kohakuterrarium", "Terrarium.status"),
    ("kohakuterrarium", "Terrarium.wire_output"),
    ("kohakuterrarium", "Terrarium.unwire_output"),
    ("kohakuterrarium", "Terrarium.list_output_wiring"),
    ("kohakuterrarium", "Terrarium.wire_output_sink"),
    ("kohakuterrarium", "Terrarium.unwire_output_sink"),
    # -- Creature -----------------------------------------------------------
    ("kohakuterrarium", "Creature.run"),
    ("kohakuterrarium", "Creature.run_stream"),
    ("kohakuterrarium", "Creature.attach"),
    ("kohakuterrarium", "Creature.chat"),
    ("kohakuterrarium", "Creature.inject_input"),
    ("kohakuterrarium", "Creature.start"),
    ("kohakuterrarium", "Creature.stop"),
    ("kohakuterrarium", "Creature.status"),
    ("kohakuterrarium", "Creature.get_status"),
    ("kohakuterrarium", "Creature.get_log_entries"),
    ("kohakuterrarium", "Creature.get_log_text"),
    # -- sessions -----------------------------------------------------------
    ("kohakuterrarium.session.reader", "SessionReader"),
    ("kohakuterrarium.session.reader", "TurnView"),
    ("kohakuterrarium", "SessionReader.events"),
    ("kohakuterrarium", "SessionReader.conversation"),
    ("kohakuterrarium", "SessionReader.channel_messages"),
    ("kohakuterrarium", "SessionReader.turns"),
    ("kohakuterrarium", "SessionReader.search"),
    ("kohakuterrarium", "SessionReader.index"),
    ("kohakuterrarium", "SessionReader.close"),
    ("kohakuterrarium", "SessionReader.meta"),
    ("kohakuterrarium", "SessionReader.agents"),
    ("kohakuterrarium", "SessionStore.open_readonly"),
    ("kohakuterrarium", "SessionStore.close"),
    # -- packages facade ----------------------------------------------------
    ("kohakuterrarium.packages", "ensure"),
    ("kohakuterrarium.packages", "install_package"),
    ("kohakuterrarium.packages", "install_package_spec"),
    ("kohakuterrarium.packages", "update_package"),
    ("kohakuterrarium.packages", "uninstall_package"),
    ("kohakuterrarium.packages", "list_packages"),
    ("kohakuterrarium.packages", "get_package_modules"),
    ("kohakuterrarium.packages", "packages_dir"),
    ("kohakuterrarium.packages", "get_package_root"),
    ("kohakuterrarium.packages", "find_package_root_for_path"),
    ("kohakuterrarium.packages", "is_package_ref"),
    ("kohakuterrarium.packages", "resolve_package_path"),
    ("kohakuterrarium.packages", "resolve_any_path"),
    ("kohakuterrarium.packages", "resolve_package_tool"),
    ("kohakuterrarium.packages", "resolve_package_io"),
    ("kohakuterrarium.packages", "resolve_package_trigger"),
    ("kohakuterrarium.packages", "resolve_package_command"),
    ("kohakuterrarium.packages", "resolve_package_user_command"),
    ("kohakuterrarium.packages", "resolve_package_prompt"),
    ("kohakuterrarium.packages", "resolve_package_skills"),
    ("kohakuterrarium.packages", "get_package_framework_hints"),
    ("kohakuterrarium.packages", "PackageError"),
    ("kohakuterrarium.packages", "PackageRefError"),
    ("kohakuterrarium.packages", "PackageNotInstalledError"),
    ("kohakuterrarium.packages", "PackagePathNotFoundError"),
    # -- compose --------------------------------------------------------------
    ("kohakuterrarium.compose", "agent"),
    ("kohakuterrarium.compose", "factory"),
    ("kohakuterrarium.compose", "AgentRunnable"),
    ("kohakuterrarium.compose", "AgentFactory"),
    ("kohakuterrarium.compose", "BaseRunnable"),
    ("kohakuterrarium.compose", "Runnable"),
    ("kohakuterrarium.compose", "Pure"),
    ("kohakuterrarium.compose", "pure"),
    ("kohakuterrarium.compose", "Sequence"),
    ("kohakuterrarium.compose", "Product"),
    ("kohakuterrarium.compose", "Fallback"),
    ("kohakuterrarium.compose", "FailsWhen"),
    ("kohakuterrarium.compose", "Retry"),
    ("kohakuterrarium.compose", "Router"),
    ("kohakuterrarium.compose", "PipelineIterator"),
    ("kohakuterrarium.compose", "BaseRunnable.retry"),
    ("kohakuterrarium.compose", "BaseRunnable.iterate"),
    ("kohakuterrarium.compose", "BaseRunnable.map"),
    ("kohakuterrarium.compose", "BaseRunnable.contramap"),
    ("kohakuterrarium.compose", "BaseRunnable.fails_when"),
    # -- validate ---------------------------------------------------------
    ("kohakuterrarium.validate", "config"),
    ("kohakuterrarium.validate", "terrarium_config"),
    ("kohakuterrarium.validate", "llm"),
    ("kohakuterrarium.validate", "creature"),
    ("kohakuterrarium.validate", "ping"),
    ("kohakuterrarium.validate", "ValidationReport"),
    # -- testing ------------------------------------------------------------
    ("kohakuterrarium.testing.llm", "ScriptedLLM"),
    ("kohakuterrarium.testing.llm", "ScriptEntry"),
    ("kohakuterrarium.testing.output", "OutputRecorder"),
    ("kohakuterrarium.testing.events", "EventRecorder"),
    ("kohakuterrarium.testing.agent", "TestAgentBuilder"),
]

# ---------------------------------------------------------------------------
# Documented signatures: (module, attr_path, documented parameter names).
# The real signature must contain every listed parameter.
# ---------------------------------------------------------------------------

DOCUMENTED_SIGNATURES: list[tuple[str, str, list[str]]] = [
    (
        "kohakuterrarium",
        "Agent.build",
        [
            "config",
            "llm",
            "pwd",
            "io",
            "strict",
            "tools",
            "plugins",
            "subagents",
            "outputs",
            "user_commands",
            "input_module",
            "output_module",
            "session",
            "environment",
        ],
    ),
    (
        "kohakuterrarium",
        "Agent.from_path",
        [
            "config_path",
            "input_module",
            "output_module",
            "session",
            "environment",
            "llm",
            "pwd",
            "strict",
            "tools",
            "plugins",
        ],
    ),
    (
        "kohakuterrarium",
        "Agent.run",
        ["content", "timeout", "source", "raise_on_error"],
    ),
    ("kohakuterrarium", "Agent.run_stream", ["content", "timeout", "source"]),
    ("kohakuterrarium", "Agent.inject_input", ["content", "source"]),
    ("kohakuterrarium", "Agent.add_tool", ["tool"]),
    ("kohakuterrarium", "Agent.add_plugin", ["plugin", "enabled"]),
    ("kohakuterrarium", "Agent.add_subagent", ["config"]),
    ("kohakuterrarium", "Terrarium", ["pwd", "session_dir"]),
    (
        "kohakuterrarium",
        "Terrarium.add_creature",
        [
            "config",
            "graph",
            "creature_id",
            "llm",
            "pwd",
            "start",
            "is_privileged",
            "parent_creature_id",
            "io",
            "strict",
            "session",
            "name",
            "tools",
            "plugins",
        ],
    ),
    ("kohakuterrarium", "Terrarium.from_recipe", ["recipe", "pwd"]),
    ("kohakuterrarium", "Terrarium.resume", ["store", "pwd", "llm"]),
    ("kohakuterrarium", "Terrarium.adopt_session", ["store", "pwd", "llm"]),
    ("kohakuterrarium", "Terrarium.with_creature", ["config", "pwd"]),
    (
        "kohakuterrarium",
        "Terrarium.apply_recipe",
        ["recipe", "graph", "pwd", "llm", "strict", "session", "creature_builder"],
    ),
    ("kohakuterrarium", "Terrarium.connect", ["sender", "receiver", "channel"]),
    ("kohakuterrarium", "Terrarium.disconnect", ["sender", "receiver", "channel"]),
    ("kohakuterrarium", "Terrarium.add_channel", ["graph", "name", "description"]),
    ("kohakuterrarium", "Terrarium.remove_channel", ["graph", "name"]),
    ("kohakuterrarium", "Terrarium.environment", ["graph"]),
    ("kohakuterrarium", "Terrarium.channel", ["graph", "name"]),
    ("kohakuterrarium", "Terrarium.subscribe", ["filter"]),
    ("kohakuterrarium", "Terrarium.attach_session", ["graph", "store"]),
    ("kohakuterrarium", "Terrarium.assign_root", ["creature", "report_channel"]),
    ("kohakuterrarium", "Terrarium.status", ["creature"]),
    ("kohakuterrarium", "SessionReader", ["path"]),
    ("kohakuterrarium", "SessionReader.events", ["agent"]),
    ("kohakuterrarium", "SessionReader.conversation", ["agent"]),
    ("kohakuterrarium", "SessionReader.channel_messages", ["channel"]),
    ("kohakuterrarium", "SessionReader.turns", ["agent"]),
    ("kohakuterrarium", "SessionReader.search", ["query", "mode", "k", "agent"]),
    ("kohakuterrarium", "SessionStore.close", ["update_status"]),
    ("kohakuterrarium.packages", "ensure", ["spec", "deps"]),
    (
        "kohakuterrarium.packages",
        "install_package",
        ["source", "editable", "name_override", "ref", "deps"],
    ),
    (
        "kohakuterrarium.packages",
        "install_package_spec",
        ["spec", "editable", "name_override", "deps"],
    ),
    ("kohakuterrarium.packages", "update_package", ["name", "deps"]),
    ("kohakuterrarium.packages", "uninstall_package", ["name"]),
    ("kohakuterrarium.compose", "agent", ["config", "engine", "pwd", "llm"]),
    ("kohakuterrarium.compose", "factory", ["config", "engine", "pwd", "llm"]),
    (
        "kohakuterrarium.compose",
        "BaseRunnable.retry",
        ["max_attempts", "backoff", "max_backoff"],
    ),
    ("kohakuterrarium.validate", "config", ["path"]),
    ("kohakuterrarium.validate", "terrarium_config", ["path"]),
    ("kohakuterrarium.validate", "llm", ["selector"]),
    ("kohakuterrarium.validate", "creature", ["path", "llm_binding"]),
    ("kohakuterrarium.validate", "ping", ["selector_or_provider", "timeout"]),
    (
        "kohakuterrarium",
        "tool",
        ["fn", "name", "description", "execution_mode"],
    ),
]

# ---------------------------------------------------------------------------
# Documented dataclass fields: (module, attr, field names).
# ---------------------------------------------------------------------------

DOCUMENTED_FIELDS: list[tuple[str, str, list[str]]] = [
    (
        "kohakuterrarium",
        "TurnResult",
        ["status", "text", "error", "tool_calls", "activities", "usage", "duration_s"],
    ),
    ("kohakuterrarium", "TextChunk", ["text"]),
    ("kohakuterrarium", "Activity", ["kind", "detail", "metadata"]),
    ("kohakuterrarium", "TurnEnded", ["result"]),
    (
        "kohakuterrarium",
        "EngineEvent",
        ["kind", "creature_id", "graph_id", "channel", "payload", "ts"],
    ),
    (
        "kohakuterrarium",
        "EventFilter",
        ["kinds", "creature_ids", "graph_ids", "channels"],
    ),
    (
        "kohakuterrarium",
        "ConnectionResult",
        ["channel", "trigger_id", "delta_kind", "graph_id"],
    ),
    ("kohakuterrarium", "DisconnectionResult", ["channels", "delta_kind"]),
    (
        "kohakuterrarium.session.reader",
        "TurnView",
        ["index", "user_text", "assistant_text", "tool_calls", "source", "ts"],
    ),
    (
        "kohakuterrarium.validate",
        "ValidationReport",
        ["name", "config_path", "model_identifier", "tools", "plugins", "subagents"],
    ),
    (
        "kohakuterrarium.testing.llm",
        "ScriptEntry",
        ["response", "match", "delay_per_chunk", "chunk_size"],
    ),
]

# The EventKind members the reference lists — engine bus is
# structure-only; content kinds live on the typed turn surface.
DOCUMENTED_EVENT_KINDS = [
    "CHANNEL_MESSAGE",
    "TOPOLOGY_CHANGED",
    "SESSION_KIND_CHANGED",
    "CREATURE_ADDED",
    "CREATURE_STARTED",
    "CREATURE_STOPPED",
    "OUTPUT_WIRE_ADDED",
    "OUTPUT_WIRE_REMOVED",
    "PARENT_LINK_CHANGED",
]

# Documented dual inheritance: typed errors that also subclass the
# builtin exception the failure historically raised.
DOCUMENTED_ERROR_BASES: list[tuple[str, type]] = [
    ("ConfigError", ValueError),
    ("ConfigNotFoundError", FileNotFoundError),
    ("PackageRefError", ValueError),
    ("PackageNotInstalledError", FileNotFoundError),
    ("PackagePathNotFoundError", FileNotFoundError),
    ("LLMNotConfiguredError", ValueError),
    ("SessionNotResumableError", ValueError),
    ("SessionNotFoundError", FileNotFoundError),
    ("TurnTimeoutError", TimeoutError),
    ("AgentNotRunningError", RuntimeError),
    ("NotFoundError", KeyError),
    ("InvalidRequestError", ValueError),
]


def _ids(entries):
    return [f"{m}:{a}" for m, a, *_ in entries]


class TestDocsPythonReference:
    """Pin docs/en/reference/python.md to the real public surface."""

    def test_reference_doc_exists(self):
        assert DOC_PATH.is_file(), f"missing reference doc: {DOC_PATH}"

    @pytest.mark.parametrize(
        ("module", "attr"),
        DOCUMENTED_SYMBOLS,
        ids=_ids(DOCUMENTED_SYMBOLS),
    )
    def test_documented_symbol_resolves(self, module, attr):
        obj = _resolve(module, attr)
        assert obj is not None

    @pytest.mark.parametrize(
        ("module", "attr"),
        DOCUMENTED_SYMBOLS,
        ids=_ids(DOCUMENTED_SYMBOLS),
    )
    def test_documented_symbol_named_in_doc(self, module, attr):
        name = attr.split(".")[-1]
        assert name in _doc_text(), (
            f"{module}.{attr} is pinned by this test but {DOC_PATH.name} "
            f"never mentions {name!r} — update the doc or drop the pin"
        )

    @pytest.mark.parametrize(
        ("module", "attr", "params"),
        DOCUMENTED_SIGNATURES,
        ids=_ids(DOCUMENTED_SIGNATURES),
    )
    def test_documented_parameters_exist(self, module, attr, params):
        obj = _resolve(module, attr)
        sig = inspect.signature(obj)
        missing = [p for p in params if p not in sig.parameters]
        assert not missing, (
            f"{module}.{attr} documented params {missing} not in real "
            f"signature {sig}"
        )

    @pytest.mark.parametrize(
        ("module", "attr", "fields"),
        DOCUMENTED_FIELDS,
        ids=_ids(DOCUMENTED_FIELDS),
    )
    def test_documented_dataclass_fields_exist(self, module, attr, fields):
        cls = _resolve(module, attr)
        actual = {f.name for f in dataclasses.fields(cls)}
        missing = [f for f in fields if f not in actual]
        assert not missing, f"{module}.{attr} lost documented fields {missing}"

    def test_turn_result_ok_property(self):
        turn_result = _resolve("kohakuterrarium", "TurnResult")
        assert isinstance(turn_result.ok, property)
        assert turn_result(status="ok").ok is True
        assert turn_result(status="error").ok is False

    def test_documented_event_kinds_exist(self):
        event_kind = _resolve("kohakuterrarium", "EventKind")
        names = {k.name for k in event_kind}
        missing = [k for k in DOCUMENTED_EVENT_KINDS if k not in names]
        assert not missing, f"EventKind lost documented members {missing}"
        # The reference also asserts these dead kinds were REMOVED —
        # the engine bus carries structure events only.
        for dead in ("TEXT", "ACTIVITY", "ERROR", "SESSION_FORKED"):
            assert dead not in names, f"EventKind.{dead} is documented as removed"

    @pytest.mark.parametrize(
        ("error_name", "builtin_base"),
        DOCUMENTED_ERROR_BASES,
        ids=[name for name, _ in DOCUMENTED_ERROR_BASES],
    )
    def test_error_hierarchy_matches_doc(self, error_name, builtin_base):
        errors = importlib.import_module("kohakuterrarium.errors")
        cls = getattr(errors, error_name)
        assert issubclass(cls, errors.KTError)
        assert issubclass(cls, builtin_base), (
            f"errors.{error_name} documented as also deriving from "
            f"{builtin_base.__name__}"
        )

    def test_packages_facade_all_is_complete(self):
        packages = importlib.import_module("kohakuterrarium.packages")
        documented = {
            attr
            for module, attr in DOCUMENTED_SYMBOLS
            if module == "kohakuterrarium.packages"
        }
        missing = documented - set(packages.__all__)
        assert not missing, f"packages.__all__ lost documented names {missing}"

    def test_compose_effects_is_deleted(self):
        # The reference deliberately documents that Effects is gone.
        compose = importlib.import_module("kohakuterrarium.compose")
        assert not hasattr(compose, "Effects")
        assert (
            importlib.util.find_spec("kohakuterrarium.compose.effects") is None
        ), "compose.effects module is documented as deleted"
