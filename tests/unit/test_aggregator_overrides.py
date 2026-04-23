"""Tests for AgentConfig.framework_hint_overrides and the aggregator's
lookup of framework-hint prose.

Covers the four canonical override keys exposed in
``kohakuterrarium.prompt.framework_hints``:

- ``framework.output_model``
- ``framework.execution_model.dynamic``
- ``framework.execution_model.static``
- ``framework.execution_model.native``

Plus package-level overrides merged via ``merge_overrides`` and the
end-to-end ``AgentInitMixin`` wiring (tested via a lightweight simulation
rather than a full agent boot).
"""

import logging

import pytest

from kohakuterrarium.prompt import aggregate_system_prompt
from kohakuterrarium.prompt.framework_hints import (
    HINT_EXECUTION_MODEL_DYNAMIC,
    HINT_EXECUTION_MODEL_NATIVE,
    HINT_EXECUTION_MODEL_STATIC,
    HINT_OUTPUT_MODEL,
    canonical_keys,
    get_framework_hint,
    merge_overrides,
)

BASE_PROMPT = "You are a helpful assistant."


# ---------------------------------------------------------------------------
# canonical key table + get_framework_hint
# ---------------------------------------------------------------------------


def test_canonical_keys_are_stable():
    """The four canonical override keys are the public contract."""
    assert set(canonical_keys()) == {
        HINT_OUTPUT_MODEL,
        HINT_EXECUTION_MODEL_DYNAMIC,
        HINT_EXECUTION_MODEL_STATIC,
        HINT_EXECUTION_MODEL_NATIVE,
    }


def test_get_framework_hint_returns_default_when_no_overrides():
    prose = get_framework_hint(HINT_EXECUTION_MODEL_DYNAMIC)
    assert prose is not None
    assert "Execution Model" in prose


def test_get_framework_hint_returns_override_when_present():
    overrides = {HINT_EXECUTION_MODEL_DYNAMIC: "CUSTOM PROSE"}
    assert get_framework_hint(HINT_EXECUTION_MODEL_DYNAMIC, overrides) == "CUSTOM PROSE"


def test_get_framework_hint_empty_string_override_kept():
    """Empty string = 'omit'; the lookup returns empty, not default."""
    overrides = {HINT_EXECUTION_MODEL_DYNAMIC: ""}
    assert get_framework_hint(HINT_EXECUTION_MODEL_DYNAMIC, overrides) == ""


def test_get_framework_hint_unknown_canonical_key_returns_none():
    assert get_framework_hint("not.a.real.key") is None


# ---------------------------------------------------------------------------
# aggregate_system_prompt integration
# ---------------------------------------------------------------------------


def test_aggregator_no_overrides_uses_defaults():
    """Baseline — the default execution-model prose appears in output."""
    prompt = aggregate_system_prompt(
        BASE_PROMPT,
        registry=None,
        include_tools=False,
        include_hints=True,
    )
    # Default dynamic execution-model block mentions this exact phrase.
    assert "Execution Model" in prompt
    # Default output-model block mentions the output wrapper syntax.
    assert "Output Format" in prompt


def test_aggregator_output_model_override_replaces_block():
    """Creature-level override for output_model wins."""
    prompt = aggregate_system_prompt(
        BASE_PROMPT,
        registry=None,
        include_tools=False,
        include_hints=True,
        framework_hint_overrides={HINT_OUTPUT_MODEL: "SHORT"},
    )
    assert "SHORT" in prompt
    assert "Output Format" not in prompt


def test_aggregator_execution_model_override_replaces_block():
    prompt = aggregate_system_prompt(
        BASE_PROMPT,
        registry=None,
        include_tools=False,
        include_hints=True,
        framework_hint_overrides={HINT_EXECUTION_MODEL_DYNAMIC: "RUN THINGS"},
    )
    assert "RUN THINGS" in prompt
    assert "Execution Model" not in prompt


def test_aggregator_empty_string_override_omits_block():
    """Empty string silences the block entirely."""
    prompt = aggregate_system_prompt(
        BASE_PROMPT,
        registry=None,
        include_tools=False,
        include_hints=True,
        framework_hint_overrides={
            HINT_OUTPUT_MODEL: "",
            HINT_EXECUTION_MODEL_DYNAMIC: "",
        },
    )
    # Neither the default nor any replacement prose is present.
    assert "Output Format" not in prompt
    assert "Execution Model" not in prompt


def test_aggregator_native_mode_override():
    """Native-mode execution model is also overridable."""
    prompt = aggregate_system_prompt(
        BASE_PROMPT,
        registry=None,
        include_tools=False,
        include_hints=True,
        tool_format="native",
        framework_hint_overrides={HINT_EXECUTION_MODEL_NATIVE: "NATIVE-MARKER"},
    )
    assert "NATIVE-MARKER" in prompt
    assert "Tool Usage" not in prompt


def test_aggregator_static_mode_override():
    prompt = aggregate_system_prompt(
        BASE_PROMPT,
        registry=None,
        include_tools=False,
        include_hints=True,
        skill_mode="static",
        framework_hint_overrides={HINT_EXECUTION_MODEL_STATIC: "STATIC-MARKER"},
    )
    assert "STATIC-MARKER" in prompt


# ---------------------------------------------------------------------------
# merge_overrides — package + creature layering
# ---------------------------------------------------------------------------


def test_merge_overrides_creature_wins():
    """Creature-level override beats package-level for the same key."""
    merged = merge_overrides(
        package_level={HINT_OUTPUT_MODEL: "PKG"},
        creature_level={HINT_OUTPUT_MODEL: "CREATURE"},
    )
    assert merged[HINT_OUTPUT_MODEL] == "CREATURE"


def test_merge_overrides_creature_and_package_keys_coexist():
    """Keys present only in one layer survive merge."""
    merged = merge_overrides(
        package_level={HINT_EXECUTION_MODEL_DYNAMIC: "PKG-EXEC"},
        creature_level={HINT_OUTPUT_MODEL: "CREATURE-OUT"},
    )
    assert merged[HINT_EXECUTION_MODEL_DYNAMIC] == "PKG-EXEC"
    assert merged[HINT_OUTPUT_MODEL] == "CREATURE-OUT"


def test_merge_overrides_handles_none_inputs():
    assert merge_overrides(None, None) == {}
    assert merge_overrides({HINT_OUTPUT_MODEL: "X"}, None) == {HINT_OUTPUT_MODEL: "X"}
    assert merge_overrides(None, {HINT_OUTPUT_MODEL: "Y"}) == {HINT_OUTPUT_MODEL: "Y"}


def test_aggregator_package_plus_creature_layering():
    """End-to-end: simulate the package+creature merge that agent_init does,
    and verify the resulting prompt reflects creature-wins semantics."""
    package_level = {HINT_OUTPUT_MODEL: "PACKAGE-OUTPUT"}
    creature_level = {HINT_OUTPUT_MODEL: "CREATURE-OUTPUT"}
    merged = merge_overrides(package_level, creature_level)
    prompt = aggregate_system_prompt(
        BASE_PROMPT,
        registry=None,
        include_tools=False,
        include_hints=True,
        framework_hint_overrides=merged,
    )
    assert "CREATURE-OUTPUT" in prompt
    assert "PACKAGE-OUTPUT" not in prompt


# ---------------------------------------------------------------------------
# Unknown-key handling — logged warning, no crash
# ---------------------------------------------------------------------------


def test_aggregator_unknown_override_key_logs_warning(caplog):
    """An unrecognised key in the override map is ignored and logged."""
    # kohakuterrarium's root logger has propagate=False by default (to keep
    # CLI output clean), which means pytest's caplog fixture can't see its
    # messages. Temporarily re-enable propagation for the assertion.
    kt_root = logging.getLogger("kohakuterrarium")
    original_propagate = kt_root.propagate
    kt_root.propagate = True
    try:
        with caplog.at_level(
            logging.WARNING, logger="kohakuterrarium.prompt.framework_hints"
        ):
            prompt = aggregate_system_prompt(
                BASE_PROMPT,
                registry=None,
                include_tools=False,
                include_hints=True,
                framework_hint_overrides={"framework.bogus.key": "IGNORED"},
            )
    finally:
        kt_root.propagate = original_propagate

    # Nothing crashed; defaults are still present.
    assert "Output Format" in prompt
    assert "IGNORED" not in prompt
    # And the framework_hints module emitted at least one warning for the
    # unknown key.
    unknown_warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING
        and "Unknown framework-hint override" in rec.getMessage()
    ]
    assert unknown_warnings, "expected a warning for the unknown override key"


# ---------------------------------------------------------------------------
# AgentConfig dataclass has the field
# ---------------------------------------------------------------------------


def test_agentconfig_has_framework_hint_overrides_field():
    from kohakuterrarium.core.config_types import AgentConfig

    cfg = AgentConfig(name="test")
    assert cfg.framework_hint_overrides == {}
    cfg.framework_hint_overrides = {HINT_OUTPUT_MODEL: "x"}
    assert cfg.framework_hint_overrides[HINT_OUTPUT_MODEL] == "x"


def test_agentconfig_loads_framework_hints_from_yaml(tmp_path):
    """The config loader reads a ``framework_hints:`` (or
    ``framework_hint_overrides:``) block from the agent's config file."""
    from kohakuterrarium.core.config import load_agent_config

    agent_dir = tmp_path / "rp_bot"
    agent_dir.mkdir()
    (agent_dir / "config.yaml").write_text(
        "name: rp_bot\n"
        "framework_hints:\n"
        "  framework.output_model: 'custom rp output'\n"
        "  framework.execution_model.dynamic: ''\n",
        encoding="utf-8",
    )

    cfg = load_agent_config(agent_dir)
    assert cfg.framework_hint_overrides[HINT_OUTPUT_MODEL] == "custom rp output"
    assert cfg.framework_hint_overrides[HINT_EXECUTION_MODEL_DYNAMIC] == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
