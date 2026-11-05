"""Initialize real creatures without changing explicit or unrelated settings."""

import pytest

from kohakuterrarium.core.config.types import AgentConfig, ToolConfigItem
from kohakuterrarium.studio.identity.module_defaults import (
    patch_defaults,
    load_defaults,
)
from kohakuterrarium.studio.sessions.module_defaults import initialize_modules
from kohakuterrarium.terrarium import Terrarium, LocalTerrariumService
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.usefixtures("native_image_gen")


@pytest.mark.parametrize("turn_budget", [{"hard": 40}, [20, 40]])
async def test_defaults_respect_creature_options_and_do_not_add_tools(
    tmp_path, turn_budget
):
    patch_defaults(
        "tool", "web_search", {"fallback": "duckduckgo", "need_content": True}
    )
    patch_defaults("tool", "web_fetch", {"fallback": "auto"})
    patch_defaults("native_tool", "image_gen", {"size": "1024x1024", "quality": "auto"})
    patch_defaults("plugin", "budget", {"walltime_budget": {"hard": 300}}, enabled=True)
    patch_defaults("plugin", "permgate", {}, enabled=True)
    config = AgentConfig(
        name="explicit",
        tools=[
            ToolConfigItem(
                name="web_search",
                options={"need_content": False, "private_token": "do-not-expose"},
            ),
            ToolConfigItem(name="image_gen", options={"quality": "high"}),
        ],
        plugins=[
            {
                "name": "budget",
                "enabled": False,
                "options": {"turn_budget": turn_budget},
            }
        ],
    )
    async with Terrarium(session_dir=tmp_path / "sessions") as engine:
        provider = ScriptedLLM([])
        provider.provider_name = "codex"
        creature = await engine.add_creature(config, llm=provider, start=False)
        service = LocalTerrariumService(engine)
        inventory = await service.list_modules(creature.creature_id)
        web = next(m for m in inventory if m["name"] == "web_search")
        assert "private_token" not in web["configured_options"]
        await initialize_modules(service, creature.creature_id, load_defaults())
        options = creature.agent.tool_options.get("web_search")
        assert options["fallback"] == "duckduckgo"
        assert options["need_content"] is False
        assert creature.agent.registry.get_tool("web_fetch") is None
        assert creature.agent.native_tool_options.get("image_gen") == {
            "size": "1024x1024",
            "quality": "high",
        }
        budget = creature.agent.plugins.get_plugin("budget")
        assert budget.get_options()["turn_budget"] == turn_budget
        assert budget.get_options()["walltime_budget"] == {"hard": 300}
        assert not creature.agent.plugins.is_enabled("budget")
        assert creature.agent.plugins.is_enabled("permgate")
        assert creature.agent.config.model == config.model
        assert creature.agent.config.triggers == config.triggers
        await initialize_modules(service, creature.creature_id, {})
        assert creature.agent.tool_options.get("web_search") == options
