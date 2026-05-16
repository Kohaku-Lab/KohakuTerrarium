"""Behavior tests for :mod:`kohakuterrarium.studio.editors.validators`.

``validators.py`` is the pydantic mirror of ``core.config_types.AgentConfig``
used at the HTTP boundary to validate creature-save bodies. The contract:

* a minimal body (just ``name``) validates and fills documented defaults;
* nested module lists coerce into their typed sub-models;
* a missing required field / wrong type raises ``ValidationError``;
* unknown keys are *allowed* (``extra="allow"``) — the framework loads
  through its own dataclasses, so the mirror must not reject forward-compat
  keys;
* ``canonical_order()`` returns every top-level field name, in declaration
  order, for YAML serialization.
"""

from pydantic import ValidationError
import pytest

from kohakuterrarium.studio.editors.validators import (
    AgentConfigIn,
    OutputConfigIn,
    SubAgentConfigItemIn,
    ToolConfigItemIn,
    canonical_order,
)


class TestAgentConfigInDefaults:
    def test_minimal_body_fills_documented_defaults(self):
        cfg = AgentConfigIn(name="alice")
        # Defaults mirror core/config_types.AgentConfig.
        assert cfg.version == "1.0"
        assert cfg.temperature == 0.7
        assert cfg.reasoning_effort == "medium"
        assert cfg.skill_mode == "dynamic"
        assert cfg.max_subagent_depth == 3
        assert cfg.system_prompt == "You are a helpful assistant."
        # Collection fields default to fresh empty containers.
        assert cfg.tools == []
        assert cfg.subagents == []
        assert cfg.triggers == []
        assert cfg.mcp_servers == []

    def test_input_and_output_default_to_their_submodels(self):
        cfg = AgentConfigIn(name="alice")
        # The nested input/output configs are real sub-models, not dicts.
        assert cfg.input.type == "cli"
        assert cfg.input.prompt == "> "
        assert isinstance(cfg.output, OutputConfigIn)
        assert cfg.output.type == "stdout"
        assert cfg.output.controller_direct is True

    def test_default_factories_are_not_shared_between_instances(self):
        a = AgentConfigIn(name="a")
        b = AgentConfigIn(name="b")
        a.tools.append(ToolConfigItemIn(name="bash"))
        # Mutating one instance's list must not bleed into the other.
        assert b.tools == []


class TestAgentConfigInRequiredFields:
    def test_missing_name_raises(self):
        with pytest.raises(ValidationError) as exc:
            AgentConfigIn()
        # The error names the missing required field.
        assert any(e["loc"] == ("name",) for e in exc.value.errors())

    def test_wrong_type_for_temperature_raises(self):
        with pytest.raises(ValidationError):
            AgentConfigIn(name="alice", temperature="hot")

    def test_unknown_top_level_key_is_allowed(self):
        # extra="allow" — forward-compat keys must survive validation.
        cfg = AgentConfigIn(name="alice", some_future_field=123)
        assert cfg.some_future_field == 123


class TestNestedModuleCoercion:
    def test_tool_dicts_coerce_to_typed_items(self):
        cfg = AgentConfigIn(
            name="alice",
            tools=[{"name": "bash"}, {"name": "read", "type": "builtin"}],
        )
        assert all(isinstance(t, ToolConfigItemIn) for t in cfg.tools)
        # Default 'type' fills in for the first entry.
        assert cfg.tools[0].type == "builtin"

    def test_tool_item_missing_name_raises(self):
        with pytest.raises(ValidationError) as exc:
            AgentConfigIn(name="alice", tools=[{"type": "builtin"}])
        # The error path points at the offending list element's name.
        assert any("name" in e["loc"] for e in exc.value.errors())

    def test_subagent_dicts_coerce_with_defaults(self):
        cfg = AgentConfigIn(
            name="alice",
            subagents=[{"name": "explore"}],
        )
        sub = cfg.subagents[0]
        assert isinstance(sub, SubAgentConfigItemIn)
        assert sub.type == "builtin"
        assert sub.can_modify is False
        assert sub.tools == []

    def test_named_outputs_coerce_recursively(self):
        cfg = AgentConfigIn(
            name="alice",
            output={"type": "stdout", "named_outputs": {"tts": {"type": "tts"}}},
        )
        assert cfg.output.named_outputs["tts"].type == "tts"


class TestCanonicalOrder:
    def test_returns_every_top_level_field(self):
        order = canonical_order()
        # Every declared field appears exactly once.
        assert set(order) == set(AgentConfigIn.model_fields.keys())
        assert len(order) == len(set(order))

    def test_declaration_order_preserved(self):
        order = canonical_order()
        # 'name' is declared first; 'output_wiring' last.
        assert order[0] == "name"
        assert order[-1] == "output_wiring"
        # 'input' is declared before 'output'.
        assert order.index("input") < order.index("output")
