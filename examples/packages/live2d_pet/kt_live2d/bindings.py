"""UI lease ownership for injected tools and explicitly configured companions."""

import asyncio
import weakref
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.skills.discovery import load_skill_from_path

from .actions import ROOT
from .control import Live2DControl, is_live2d_tool

SKILL_NAME = "live2d-expression"
SKILL_PATH = ROOT / "skills" / SKILL_NAME / "SKILL.md"
bindings = weakref.WeakKeyDictionary()


@dataclass
class Attachment:
    agent: Any
    tool: Any
    skill: Any
    owns_tool: bool
    skill_enabled: bool
    expiry: Any = None


def remove_binding(engine):
    previous = bindings.pop(engine, None)
    if previous is None:
        return
    if previous.expiry:
        previous.expiry.cancel()
    previous.tool.revoke()
    agent = previous.agent
    if agent.skills.get(SKILL_NAME) is previous.skill:
        previous.skill.enabled = previous.skill_enabled
    if previous.owns_tool and agent.registry.get_tool("l2d") is previous.tool:
        agent.registry.unregister_tool("l2d")
        agent.executor.unregister_tool("l2d")
    agent.refresh_system_prompt()


def attach(engine, agent, lease, mode):
    """Attach without replacing configured tools or persisting temporary skill toggles."""
    current = bindings.get(engine)
    tool = agent.registry.get_tool("l2d")
    if tool is not None and not is_live2d_tool(tool):
        raise ValueError("This creature already owns another l2d tool.")
    configured = tool is not None and not (
        current and current.agent is agent and current.owns_tool
    )
    if mode == "agent" and (not configured or tool.purpose != "companion"):
        raise ValueError(
            "Select the Live2D Companion creature before enabling dedicated mode."
        )
    if current and current.agent is agent and current.tool.lease == lease:
        return current
    skill = agent.skills.get(SKILL_NAME)
    if skill is not None and skill.base_dir != SKILL_PATH.parent.resolve():
        raise ValueError("This creature already owns another Live2D skill.")
    loaded = skill or load_skill_from_path(
        SKILL_PATH, origin="extension:live2d-pet-demo"
    )
    if loaded is None or loaded.name != SKILL_NAME:
        raise ValueError("The Live2D skill is unavailable.")
    remove_binding(engine)
    tool = agent.registry.get_tool("l2d")
    attachment = Attachment(
        agent,
        tool or Live2DControl(),
        loaded,
        tool is None,
        bool(skill and skill.enabled),
    )
    if skill is None:
        agent.skills.add(loaded)
    loaded.enabled = True
    if attachment.owns_tool:
        agent.add_tool(attachment.tool)
    else:
        agent.refresh_system_prompt()
    bindings[engine] = attachment
    return attachment


def renew(engine, attachment):
    if attachment.expiry:
        attachment.expiry.cancel()
    attachment.expiry = asyncio.get_running_loop().call_later(
        20, remove_binding, engine
    )
