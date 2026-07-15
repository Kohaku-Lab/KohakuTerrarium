"""Pre-flight validation — answer "is my setup correct?" loudly.

Each helper raises a typed error (:mod:`kohakuterrarium.errors`) on the
first failure, allowing scripts and CI to use validation as a gate::

    import kohakuterrarium as kt

    kt.validate.config("./scoring-agent")          # config parses + base resolves
    kt.validate.llm("openai/gpt-5@reasoning=high") # profile + credentials resolve
    report = kt.validate.creature("./scoring-agent")  # full build dry-run
    await kt.validate.ping("openai/gpt-5")         # one real round-trip

``kt doctor`` is the CLI wrapper over the same functions.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.errors import LLMNotConfiguredError
from kohakuterrarium.bootstrap.llm import _create_from_profile, coerce_llm_provider
from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.builtins.outputs.none import NoneOutput
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import AgentConfig, load_agent_config
from kohakuterrarium.llm.profiles import profile_to_identifier, resolve_controller_llm
from kohakuterrarium.terrarium.config import TerrariumConfig, load_terrarium_config
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ValidationReport:
    """Result of a successful :func:`creature` dry-run build."""

    name: str
    config_path: str
    model_identifier: str
    tools: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)


def config(path: "str | Path") -> AgentConfig:
    """Load an agent config and resolve its package and base-config references."""
    return load_agent_config(path)


def terrarium_config(path: "str | Path") -> TerrariumConfig:
    """Load and validate a terrarium recipe path or package reference."""
    return load_terrarium_config(path)


def llm(selector: str | None = None) -> str:
    """Resolve an LLM selector and construct its provider without a network call.

    Args:
        selector: Profile or preset name, or ``provider/model[@variations]``.
            ``None`` validates the configured default model.

    Returns:
        The canonical ``provider/name[@variations]`` identifier.

    Raises:
        LLMNotConfiguredError: No profile resolves for the selector.
        ValueError: The profile resolves but credentials are missing.
    """
    profile = resolve_controller_llm({}, llm=selector)
    if profile is None:
        raise LLMNotConfiguredError(
            f"No LLM profile resolves for {selector!r}"
            if selector
            else "No default model configured — run: kt model default <name>"
        )
    _create_from_profile(profile)
    return profile_to_identifier(profile)


def creature(path: "str | Path", *, llm_binding: Any = None) -> ValidationReport:
    """Build but do not start a creature, validating all configured components.

    The strict headless build resolves config inheritance, the LLM, tools,
    plugins, and sub-agent configs.

    Args:
        path: Creature config folder or package reference.
        llm_binding: Optional provider, selector, or profile that overrides the
            config's LLM binding.

    Returns:
        A :class:`ValidationReport` describing what was built.
    """
    cfg = load_agent_config(path)
    agent = Agent(
        cfg,
        llm=llm_binding,
        input_module=NoneInput(),
        output_module=NoneOutput(),
        strict=True,
    )
    plugin_names: list[str] = []
    if agent.plugins is not None:
        plugin_names = sorted(p.get("name", "") for p in agent.plugins.list_plugins())
    return ValidationReport(
        name=cfg.name,
        config_path=str(cfg.agent_path),
        model_identifier=agent.llm_identifier(),
        tools=sorted(agent.registry.list_tools()),
        plugins=plugin_names,
        subagents=sorted(agent.subagent_manager.list_subagents() or []),
    )


async def ping(selector_or_provider: Any = None, *, timeout: float = 30.0) -> str:
    """Make one real LLM round trip and return its text response.

    This is the only validator that accesses the network; provider, transport,
    and credential failures propagate.
    """
    cfg = AgentConfig(name="_kt_ping")
    provider = coerce_llm_provider(selector_or_provider, cfg)
    reply = await asyncio.wait_for(
        provider.chat_complete(
            [{"role": "user", "content": "Reply with exactly: pong"}]
        ),
        timeout=timeout,
    )
    text = getattr(reply, "content", None) or str(reply)
    logger.info("LLM ping ok", model=getattr(provider, "model", ""), reply=text[:80])
    return text
