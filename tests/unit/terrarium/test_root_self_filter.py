"""Regression tests for root-agent channel self-filter (bug #1).

The terrarium ``factory.build_root_agent`` previously hardcoded
``ignore_sender="root"`` when wiring root's channel triggers.  However
``send_message`` stamps the outgoing ``ChannelMessage.sender`` with
``ToolContext.agent_name``, which is ``agent.config.name`` — i.e. the
literal ``name:`` field in the root creature config.  Almost no real
root agent is named ``root`` (operators give them personas / handles),
so the filter silently missed every self-message and the root agent
received its own messages back as new triggers.

These tests use the ChannelTrigger directly to isolate the
sender-id mismatch.  We don't need a full Agent stack to prove the
fix; the mismatch lives in the byte-equal compare inside
``ChannelTrigger.wait_for_trigger``.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from kohakuterrarium.core.channel import ChannelMessage, ChannelRegistry
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.terrarium import factory as factory_mod
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    RootConfig,
    TerrariumConfig,
)


async def _drain(trigger: ChannelTrigger, timeout: float = 0.2) -> list:
    """Pull events from a trigger for a short window."""
    events: list = []
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            evt = await asyncio.wait_for(
                trigger.wait_for_trigger(), timeout=min(remaining, 0.05)
            )
        except asyncio.TimeoutError:
            continue
        if evt is None:
            break
        events.append(evt)
    return events


@pytest.mark.asyncio
async def test_channel_trigger_filters_byte_equal_sender():
    """The filter is a strict byte-equal compare — sanity baseline."""
    registry = ChannelRegistry()
    trigger = ChannelTrigger(
        channel_name="bus",
        subscriber_id="alice",
        ignore_sender="alice",
        registry=registry,
    )
    await trigger.start()

    channel = registry.get_or_create("bus", channel_type="queue")
    await channel.send(ChannelMessage(sender="alice", content="hi"))
    await channel.send(ChannelMessage(sender="bob", content="hello"))

    events = await _drain(trigger)
    await trigger.stop()

    senders = [e.context["sender"] for e in events]
    assert senders == ["bob"], senders


@pytest.mark.asyncio
async def test_channel_trigger_drops_only_exact_match():
    """A different-cased sender is NOT filtered (regression baseline).

    This documents the byte-equal contract.  The factory must hand the
    trigger the *exact* string ``send_message`` will stamp; any
    normalization mismatch (case, whitespace) leaks through.
    """
    registry = ChannelRegistry()
    trigger = ChannelTrigger(
        channel_name="bus",
        subscriber_id="root",
        ignore_sender="root",
        registry=registry,
    )
    await trigger.start()

    channel = registry.get_or_create("bus", channel_type="queue")
    # Operator named their root creature "Root" (capital R).
    # send_message stamps "Root", filter is "root" → leaks through.
    await channel.send(ChannelMessage(sender="Root", content="from-self"))

    events = await _drain(trigger)
    await trigger.stop()

    # Confirms the bug shape: ignore_sender must equal the stamped sender.
    assert len(events) == 1
    assert events[0].context["sender"] == "Root"


def test_factory_uses_agent_config_name_for_root_self_filter(tmp_path, monkeypatch):
    """``build_root_agent`` wires ignore_sender from agent.config.name.

    Pre-fix, the factory hardcoded ``"root"``.  Post-fix it pulls the
    identifier from the live Agent's config so it byte-matches what
    ``send_message`` stamps via ``ToolContext.agent_name``.
    """
    # Capture the args of _inject_channel_triggers
    captured: dict = {}

    def _capture(**kwargs) -> None:
        captured.update(kwargs)

    # Build a TerrariumConfig with a root.  The Agent itself is mocked
    # so we don't have to spin up the full LLM / executor stack.
    root_data = {
        "name": "kohaku-coordinator",  # NOT "root" — the bug case
        "model": "test/model",
        "system_prompt": "x",
    }
    config = TerrariumConfig(
        name="t",
        creatures=[],
        channels=[ChannelConfig(name="bus", channel_type="queue")],
        root=RootConfig(config_data=root_data, base_dir=tmp_path),
    )
    env = Environment(env_id="test")

    fake_agent = MagicMock()
    fake_agent.config.name = "kohaku-coordinator"
    fake_agent.registry.get_tool.return_value = object()  # skip force-register

    with (
        patch.object(factory_mod, "Agent", return_value=fake_agent),
        patch.object(factory_mod, "_inject_channel_triggers", side_effect=_capture),
        patch.object(factory_mod, "force_register_terrarium_tools"),
        patch.object(factory_mod, "inject_prompt_section"),
        patch.object(
            factory_mod, "build_agent_config", return_value=MagicMock(name="agent_cfg")
        ),
    ):
        runtime = MagicMock()
        factory_mod.build_root_agent(config, env, runtime)

    assert captured.get("ignore_sender") == "kohaku-coordinator"
    assert captured.get("subscriber_id") == "kohaku-coordinator"


def test_factory_uses_agent_config_name_for_creature_self_filter(tmp_path):
    """Same byte-equal contract for creatures.

    The creature factory previously used ``creature_cfg.name``; if the
    user's creature ``name:`` field differs from the terrarium-level
    handle (rare but possible via base_config), the filter would miss.
    Post-fix the trigger is wired from ``agent.config.name`` directly.
    """
    captured: dict = {}

    def _capture(**kwargs) -> None:
        captured.update(kwargs)

    creature_cfg = CreatureConfig(
        name="reviewer",
        config_data={"name": "reviewer", "model": "x", "system_prompt": "y"},
        base_dir=tmp_path,
        listen_channels=["bus"],
        send_channels=[],
    )
    config = TerrariumConfig(
        name="t",
        creatures=[creature_cfg],
        channels=[ChannelConfig(name="bus", channel_type="queue")],
    )
    env = Environment(env_id="test")

    fake_agent = MagicMock()
    fake_agent.config.name = "reviewer"

    with (
        patch.object(factory_mod, "Agent", return_value=fake_agent),
        patch.object(factory_mod, "_inject_channel_triggers", side_effect=_capture),
        patch.object(
            factory_mod, "build_agent_config", return_value=MagicMock(name="agent_cfg")
        ),
        patch.object(factory_mod, "build_channel_topology_prompt", return_value=""),
    ):
        factory_mod.build_creature(creature_cfg, env, config)

    assert captured.get("ignore_sender") == "reviewer"
    assert captured.get("subscriber_id") == "reviewer"
