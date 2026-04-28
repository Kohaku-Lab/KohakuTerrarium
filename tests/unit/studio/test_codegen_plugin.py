"""Plugin codegen — scaffold, parse, re-render with changed hooks."""

from kohakuterrarium.studio.editors import codegen_plugin as plugin_cg

SAMPLE_PLUGIN = '''\
"""Response logger."""

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ResponseLoggerPlugin(BasePlugin):
    name = "response_logger"
    priority = 50

    async def pre_tool_execute(self, args: dict, **kwargs):
        logger.info("tool pre", name=kwargs.get("tool_name"))
        return None

    async def post_tool_execute(self, result, **kwargs):
        logger.info("tool post", name=kwargs.get("tool_name"))
        return None
'''


def test_render_new_compiles():
    source = plugin_cg.render_new(
        {
            "name": "my_plugin",
            "class_name": "MyPlugin",
            "priority": 30,
            "description": "test",
            "enabled_hooks": [
                {"name": "pre_tool_execute", "body": "return None"},
            ],
        }
    )
    compile(source, "<rendered>", "exec")
    assert "class MyPlugin(BasePlugin)" in source
    assert "pre_tool_execute" in source
    assert "priority = 30" in source


def test_render_new_with_multiple_hooks():
    source = plugin_cg.render_new(
        {
            "name": "p",
            "priority": 50,
            "description": "t",
            "enabled_hooks": [
                {"name": "pre_tool_execute", "body": "return None"},
                {"name": "post_llm_call", "body": "return None"},
                {"name": "on_agent_start", "body": "return None"},
            ],
        }
    )
    compile(source, "<rendered>", "exec")
    assert "pre_tool_execute" in source
    assert "post_llm_call" in source
    assert "on_agent_start" in source


def test_parse_back_extracts_hooks():
    env = plugin_cg.parse_back(SAMPLE_PLUGIN)
    assert env["mode"] == "simple"
    form = env["form"]
    assert form["class_name"] == "ResponseLoggerPlugin"
    assert form["name"] == "response_logger"
    assert form["priority"] == 50
    hook_names = [h["name"] for h in form["enabled_hooks"]]
    assert set(hook_names) == {"pre_tool_execute", "post_tool_execute"}


def test_parse_back_missing_class():
    env = plugin_cg.parse_back("# no class here\nx = 1\n")
    assert env["mode"] == "raw"


def test_update_existing_preserves_hook_body():
    """Round-trip keeps the original body when not overwritten."""
    new_src = plugin_cg.update_existing(
        SAMPLE_PLUGIN,
        {
            "name": "response_logger",
            "class_name": "ResponseLoggerPlugin",
            "priority": 50,
            "description": "desc",
            "enabled_hooks": [
                # Keep pre_tool_execute, drop post_tool_execute
                {"name": "pre_tool_execute", "body": ""},
            ],
        },
        "",
    )
    compile(new_src, "<updated>", "exec")
    assert "pre_tool_execute" in new_src
    # post_tool_execute removed
    assert "post_tool_execute" not in new_src
    # Original body preserved (because caller sent body="")
    assert "tool pre" in new_src


def test_update_existing_replaces_body_when_provided():
    new_src = plugin_cg.update_existing(
        SAMPLE_PLUGIN,
        {
            "name": "response_logger",
            "class_name": "ResponseLoggerPlugin",
            "priority": 50,
            "description": "desc",
            "enabled_hooks": [
                {"name": "pre_tool_execute", "body": 'logger.info("renamed")'},
            ],
        },
        "",
    )
    compile(new_src, "<updated>", "exec")
    assert "renamed" in new_src
    assert "tool pre" not in new_src


def test_update_existing_adds_new_hook():
    new_src = plugin_cg.update_existing(
        SAMPLE_PLUGIN,
        {
            "name": "response_logger",
            "class_name": "ResponseLoggerPlugin",
            "priority": 50,
            "description": "desc",
            "enabled_hooks": [
                {"name": "pre_tool_execute", "body": ""},
                {"name": "post_tool_execute", "body": ""},
                {"name": "on_agent_start", "body": 'logger.info("started")'},
            ],
        },
        "",
    )
    compile(new_src, "<updated>", "exec")
    assert "on_agent_start" in new_src
    assert "started" in new_src


def test_roundtrip_identity():
    env = plugin_cg.parse_back(SAMPLE_PLUGIN)
    regen = plugin_cg.update_existing(
        SAMPLE_PLUGIN,
        env["form"],
        "",
    )
    env2 = plugin_cg.parse_back(regen)
    h1 = sorted([h["name"] for h in env["form"]["enabled_hooks"]])
    h2 = sorted([h["name"] for h in env2["form"]["enabled_hooks"]])
    assert h1 == h2
