"""Unit tests for live provider-tool synchronization."""

from types import SimpleNamespace

from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.provider_tools import sync_provider_tools
from kohakuterrarium.core.registry import Registry


def _agent(*, provider="", offered=(), configured=()):
    registry = Registry()
    executor = Executor()
    for name in configured:
        tool = get_builtin_tool(name)
        registry.register_tool(tool)
        executor.register_tool(tool)
    agent = SimpleNamespace(
        registry=registry,
        executor=executor,
        llm=SimpleNamespace(
            provider_name=provider,
            provider_native_tools=frozenset(offered),
        ),
        config=AgentConfig(name="a"),
        _auto_provider_tools=set(),
        refresh_count=0,
    )
    agent.refresh_system_prompt = lambda: setattr(
        agent, "refresh_count", agent.refresh_count + 1
    )
    return agent


def test_adds_grok_media_tools_to_registry_and_executor():
    agent = _agent(
        provider="grok-subscription",
        offered=("grok_image_gen", "video_gen"),
    )

    sync_provider_tools(agent)

    expected = {"grok_image_gen", "video_gen"}
    assert set(agent.registry.list_tools()) == expected
    assert set(agent.executor.list_tools()) == expected
    assert agent._auto_provider_tools == expected
    assert agent.refresh_count == 1


def test_removes_only_stale_auto_injected_local_tools():
    agent = _agent(provider="openai", configured=("bash",))
    image_tool = get_builtin_tool("grok_image_gen")
    agent.registry.register_tool(image_tool)
    agent.executor.register_tool(image_tool)
    agent._auto_provider_tools.add("grok_image_gen")

    sync_provider_tools(agent)

    assert "grok_image_gen" not in agent.registry.list_tools()
    assert "grok_image_gen" not in agent.executor.list_tools()
    assert "bash" in agent.registry.list_tools()
    assert "bash" in agent.executor.list_tools()


def test_keeps_explicit_local_provider_tool():
    agent = _agent(provider="openai", configured=("grok_image_gen",))

    sync_provider_tools(agent)

    assert "grok_image_gen" in agent.registry.list_tools()
    assert "grok_image_gen" in agent.executor.list_tools()


def test_removes_explicit_native_tool_unsupported_by_new_provider():
    agent = _agent(provider="openai", configured=("image_gen",))

    sync_provider_tools(agent)

    assert "image_gen" not in agent.registry.list_tools()
    assert "image_gen" not in agent.executor.list_tools()
