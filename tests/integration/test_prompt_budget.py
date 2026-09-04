"""Budget gate for everything the framework injects into a turn.

One fat workflow: build the reference creature both ways, measure the whole
per-turn payload (system prompt plus native tool schemas), and assert the size
targets alongside the structural invariants that produce them.

The fixture mirrors the shipped ``@kt-biome/creatures/general`` loadout but
carries no package references, so this runs on the CI matrix where kt-biome is
not installed. When that loadout changes, this fixture changes with it.
"""

import json
from pathlib import Path

import pytest

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.llm.tools import build_tool_schemas
from kohakuterrarium.testing.llm import ScriptedLLM

FIXTURE = Path(__file__).parent.parent / "fixtures" / "creatures" / "budget_ref"

# Chars, not tokens: char counts are deterministic and need no tokenizer.
# ~4 chars per token, so these are roughly 4k and 16k tokens.
MAX_STANDARD_CHARS = 16_000
MAX_FULL_CHARS = 64_000

MAX_CALLABLES = 20
# One nested-array schema (show_card: fields + actions) legitimately needs
# more than a flat one; the aggregate budget above is the real constraint.
MAX_SCHEMA_CHARS = 1_500
MAX_TOOL_GUIDANCE_CHARS = 600

BACKGROUND_CAPABLE = {"bash", "python", "web_fetch", "web_search"}


def _measure(agent, mode):
    """Return (framework_chars, schemas) for one built agent."""
    base = agent.config.system_prompt
    prompt = agent._controller_config.system_prompt
    schemas = build_tool_schemas(agent.registry, tool_doc_mode=mode)
    schema_chars = sum(
        len(s.name) + len(s.description) + len(json.dumps(s.parameters))
        for s in schemas
    )
    return (len(prompt) - len(base)) + schema_chars, schemas


class TestPromptBudget:
    @pytest.mark.asyncio
    async def test_framework_payload_stays_within_budget(self):
        config = load_agent_config(str(FIXTURE))
        assert config.tool_format == "native"
        # kt-biome ships `full`; both modes are measured explicitly below so
        # the gate does not depend on which one is the current default.
        assert config.tool_doc_mode == "full"

        # ── standard: the lean mode ────────────────────────────────────
        config.tool_doc_mode = "standard"
        agent = await Agent.build(
            config, llm=ScriptedLLM(["ok"]), io="headless", strict=False
        )
        try:
            standard_chars, schemas = _measure(agent, "standard")
            prompt = agent._controller_config.system_prompt

            assert standard_chars <= MAX_STANDARD_CHARS, (
                f"framework payload is {standard_chars} chars "
                f"(~{standard_chars // 4} tok), budget {MAX_STANDARD_CHARS}"
            )

            # Tool selection degrades past roughly twenty callables.
            assert len(schemas) <= MAX_CALLABLES, [s.name for s in schemas]

            # Native providers carry the inventory as schema; the prompt must
            # not repeat it, in prose or as a sub-agent list.
            assert "## Available Functions" not in prompt
            assert "## Available Sub-Agents" not in prompt

            # No call-syntax prose for a format that has none.
            assert "## Calling functions" not in prompt
            assert "## Output format" not in prompt

            # Solo creature: no graph sections at all.
            assert "Working with the group" not in prompt
            assert "Growing the group" not in prompt
            assert "Internal Channels" not in prompt

            # Framework semantics stated once, not per schema.
            payload = prompt + json.dumps([s.parameters for s in schemas])
            assert payload.count("context-isolated") == 1
            assert "continue the previous task" in prompt

            for schema in schemas:
                blob = json.dumps(schema.parameters)
                assert len(blob) <= MAX_SCHEMA_CHARS, f"{schema.name}: {len(blob)}"
                has_bg = "run_in_background" in schema.parameters.get("properties", {})
                is_subagent = schema.name in set(agent.registry.list_subagents())
                if not is_subagent:
                    assert has_bg == (
                        schema.name in BACKGROUND_CAPABLE
                    ), f"{schema.name} background flag is wrong"

            if "## Tool guidance" in prompt:
                section = prompt.split("## Tool guidance", 1)[1].split("\n## ", 1)[0]
                assert len(section) <= MAX_TOOL_GUIDANCE_CHARS

            # Descriptions are the always-loaded tier and stay capped.
            for schema in schemas:
                assert len(schema.description) <= 160, schema.name
        finally:
            await agent.stop()

        # ── full: every usage tier inlined; the shipped default ────────
        config.tool_doc_mode = "full"
        agent = await Agent.build(
            config, llm=ScriptedLLM(["ok"]), io="headless", strict=False
        )
        try:
            full_chars, _ = _measure(agent, "full")
            prompt = agent._controller_config.system_prompt
            assert full_chars <= MAX_FULL_CHARS, (
                f"full payload is {full_chars} chars "
                f"(~{full_chars // 4} tok), budget {MAX_FULL_CHARS}"
            )
            assert "## Function Documentation" in prompt
            # Reference material never inlines, in any mode.
            assert "## Reference" not in prompt
            assert full_chars > standard_chars
        finally:
            await agent.stop()

    @pytest.mark.asyncio
    async def test_per_tool_full_override_costs_only_that_tool(self):
        # A creature on `standard` can still opt one tool into the usage tier,
        # and pays for that tool alone.
        config = load_agent_config(str(FIXTURE))
        config.tool_doc_mode = "standard"
        for entry in config.tools:
            entry.doc_mode = "full" if entry.name == "multi_edit" else None

        agent = await Agent.build(
            config, llm=ScriptedLLM(["ok"]), io="headless", strict=False
        )
        try:
            prompt = agent._controller_config.system_prompt
            assert "## Function Documentation" in prompt
            section = prompt.split("## Function Documentation", 1)[1]
            assert "multi_edit" in section
            assert "# grep" not in section
            assert "# bash" not in section
        finally:
            await agent.stop()
