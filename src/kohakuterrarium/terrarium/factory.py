"""
Creature and root agent construction.

Pure functions that build Agent instances from config, wire channel
triggers, inject topology prompts, and attach output log captures.
Extracted from TerrariumRuntime to keep the runtime focused on
lifecycle orchestration.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import build_agent_config
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.terrarium.config import (
    CreatureConfig,
    TerrariumConfig,
    build_channel_topology_prompt,
)
from kohakuterrarium.terrarium.creature import CreatureHandle
from kohakuterrarium.terrarium.output_log import OutputLogCapture
from kohakuterrarium.terrarium.tool_manager import (
    TERRARIUM_MANAGER_KEY,
    TerrariumToolManager,
)
from kohakuterrarium.terrarium.tool_registry import ensure_terrarium_tools_registered
from kohakuterrarium.terrarium.tool_runtime_factory import create_runtime_from_config
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.runtime import TerrariumRuntime

logger = get_logger(__name__)


def _inject_channel_triggers(
    agent: Agent,
    subscriber_id: str,
    channel_names: list[str],
    prompts: dict[str, str],
    ignore_sender: str,
    registry: Any,
    config: TerrariumConfig,
) -> None:
    """Create ChannelTrigger objects and register them on an agent.

    Shared helper used by both ``build_root_agent`` and ``build_creature``
    to wire channel triggers into an agent's trigger_manager.

    Args:
        agent: The agent to inject triggers into.
        subscriber_id: Unique subscriber prefix (e.g. "root" or creature name).
        channel_names: Channel names to listen on.
        prompts: Mapping of channel name to prompt template string.
        ignore_sender: Sender name to ignore (avoids echo).
        registry: The shared channel registry.
        config: The terrarium config (used for broadcast name lookup in logs).
    """
    broadcast_names = {
        ch.name for ch in config.channels if ch.channel_type == "broadcast"
    }
    for ch_name in channel_names:
        prompt = prompts.get(ch_name, "[Channel '{channel}' from {sender}]: {content}")
        trigger = ChannelTrigger(
            channel_name=ch_name,
            subscriber_id=f"{subscriber_id}_{ch_name}",
            prompt=prompt,
            ignore_sender=ignore_sender,
            registry=registry,
        )
        trigger_id = f"channel_{subscriber_id}_{ch_name}"
        agent.trigger_manager._triggers[trigger_id] = trigger
        agent.trigger_manager._created_at[trigger_id] = datetime.now()
        logger.debug(
            "Injected channel trigger",
            subscriber=subscriber_id,
            channel=ch_name,
            trigger_id=trigger_id,
            broadcast=ch_name in broadcast_names,
        )


def build_root_agent(
    config: TerrariumConfig,
    environment: Environment,
    runtime: "TerrariumRuntime",
    **kwargs: Any,
) -> Agent:
    """
    Build the root agent OUTSIDE the terrarium.

    The root agent:
    - Loads from its own creature config (e.g. creatures/root)
    - Gets a TerrariumToolManager pre-bound to this runtime
    - Has its own I/O (cli/tui) for user interaction
    - Is NOT a peer of terrarium creatures

    Args:
        config: The terrarium config (must have ``root`` set).
        environment: The terrarium's shared environment.
        runtime: The TerrariumRuntime instance to bind tools to.

    Returns:
        A fully wired root Agent.
    """
    root_cfg = config.root
    assert root_cfg is not None

    logger.info("Building root agent")

    # Build root agent config from inline dict (supports base_config inheritance)
    agent_config = build_agent_config(root_cfg.config_data, root_cfg.base_dir)

    # Create a separate environment for the root agent
    # with a TerrariumToolManager pre-bound to this runtime
    root_env = Environment(env_id=f"root_{environment.env_id}")
    manager = TerrariumToolManager(
        runtime_factory=create_runtime_from_config,
        runtime_class=type(runtime),
    )
    manager.register_runtime(config.name, runtime)
    root_env.register(TERRARIUM_MANAGER_KEY, manager)

    root_session = root_env.get_session("root")

    # Root agent uses NoneInput by default (terrarium TUI handles I/O).
    # If running headless (web API), this is correct.
    # If running with TUI, the terrarium CLI creates the TUI separately.
    agent = Agent(
        agent_config,
        input_module=NoneInput(),
        session=root_session,
        environment=root_env,
        llm_override=kwargs.get("llm_override"),
        pwd=kwargs.get("pwd"),
    )

    # Force-add all terrarium tools regardless of creature config
    force_register_terrarium_tools(agent)

    # Inject terrarium awareness into root's system prompt
    awareness = build_root_awareness_prompt(config)
    inject_prompt_section(agent, awareness)

    # Auto-inject channel triggers for ALL channels (root hears everything)
    root_prompts: dict[str, str] = {}
    for ch in config.channels:
        if ch.channel_type == "broadcast":
            root_prompts[ch.name] = (
                "[Channel '{channel}' (broadcast) from {sender}]: {content}\n\n"
                "You are listening to all channels as the team coordinator. "
                "This was broadcast on '{channel}'. "
                "You do NOT need to respond to every message."
            )
        else:
            root_prompts[ch.name] = (
                "[Channel '{channel}' from {sender}]: {content}\n\n"
                "A message arrived on '{channel}'. "
                "Evaluate if you need to act on it or relay information."
            )
    _inject_channel_triggers(
        agent=agent,
        subscriber_id="root",
        channel_names=[ch.name for ch in config.channels],
        prompts=root_prompts,
        ignore_sender="root",
        registry=environment.shared_channels,
        config=config,
    )

    return agent


def force_register_terrarium_tools(agent: Agent) -> None:
    """Force-register all terrarium management tools on the root agent.

    Calls ``ensure_terrarium_tools_registered()`` to make sure the
    terrarium tool decorators have fired before looking them up.
    """
    ensure_terrarium_tools_registered()

    terrarium_tool_names = [
        "terrarium_create",
        "terrarium_status",
        "terrarium_stop",
        "terrarium_send",
        "terrarium_history",
        "creature_start",
        "creature_stop",
        "creature_interrupt",
    ]
    for name in terrarium_tool_names:
        if agent.registry.get_tool(name) is None:
            tool = get_builtin_tool(name)
            if tool:
                agent.registry.register_tool(tool)
                agent.executor.register_tool(tool)
                logger.debug("Force-registered terrarium tool", tool_name=name)


def build_root_awareness_prompt(config: TerrariumConfig) -> str:
    """Build prompt section telling root about the bound terrarium."""
    creature_names = [c.name for c in config.creatures]

    channel_lines: list[str] = []
    for ch in config.channels:
        desc = f" - {ch.description}" if ch.description else ""
        channel_lines.append(f"- `{ch.name}` ({ch.channel_type}){desc}")

    # Document auto-created direct channels
    direct_lines: list[str] = []
    for name in creature_names:
        direct_lines.append(f"- `{name}` (queue) - direct channel to {name}")

    parts = [
        f"## Bound Terrarium: {config.name}",
        "",
        f"Use terrarium_id='{config.name}' for all terrarium tool calls.",
        "",
        "### Auto-Listening",
        "",
        "You automatically listen to ALL channels in this terrarium.",
        "Messages arrive as trigger events in this format:",
        "",
        "  [Channel 'channel_name' from sender_name]: message content",
        "  [Channel 'channel_name' (broadcast) from sender_name]: content",
        "",
        "**Hearing a message does NOT mean you must respond.**",
        "You are the coordinator. Evaluate each message:",
        "- Results channels: summarize for the user when relevant",
        "- Broadcast channels: absorb context, only act if directly relevant",
        "- Task channels: usually handled by creatures, not you",
        "",
        "### Creatures",
        ", ".join(creature_names),
        "",
        "### Channels",
        *channel_lines,
        "",
        "### Direct Channels",
        "Every creature has a direct queue channel named after it.",
        "Use these to send messages to a specific creature:",
        *direct_lines,
    ]
    return "\n".join(parts)


def build_creature(
    creature_cfg: CreatureConfig,
    environment: Environment,
    config: TerrariumConfig,
    **kwargs: Any,
) -> CreatureHandle:
    """
    Build a single creature: load config, create Agent, wire channels.

    Args:
        creature_cfg: Configuration for this creature.
        environment: The terrarium's shared environment.
        config: The full terrarium config (needed for topology prompt).

    Returns:
        A fully wired CreatureHandle.
    """
    logger.info(
        "Building creature",
        creature=creature_cfg.name,
    )

    # Auto-inject report_to_root into send channels when root exists
    if config.root and "report_to_root" not in creature_cfg.send_channels:
        creature_cfg.send_channels.append("report_to_root")

    # Build agent config from inline dict (same format as standalone)
    agent_config = build_agent_config(creature_cfg.config_data, creature_cfg.base_dir)

    # Each creature gets a PRIVATE session from the environment
    creature_session = environment.get_session(creature_cfg.name)

    # For creatures with no interactive user input, override input
    # to NoneInput so the agent loop blocks on triggers instead of stdin.
    input_module = NoneInput()

    # Create the agent with explicit session and environment
    agent = Agent(
        agent_config,
        input_module=input_module,
        session=creature_session,
        environment=environment,
        llm_override=kwargs.get("llm_override"),
        pwd=kwargs.get("pwd"),
    )

    # -- Inject ChannelTriggers for listen channels --
    # Always listen on the creature's own direct channel
    all_listen = list(creature_cfg.listen_channels)
    if creature_cfg.name not in all_listen:
        all_listen.append(creature_cfg.name)

    # Broadcast channels get a prompt that frames messages as informational
    broadcast_names = {
        ch.name for ch in config.channels if ch.channel_type == "broadcast"
    }
    creature_prompts: dict[str, str] = {}
    for ch_name in all_listen:
        if ch_name in broadcast_names:
            creature_prompts[ch_name] = (
                "[Channel '{channel}' (broadcast) from {sender}]: {content}\n\n"
                "This was broadcast to all listeners on '{channel}'. "
                "Only respond if relevant to your current task."
            )
        else:
            creature_prompts[ch_name] = "[Channel '{channel}' from {sender}]: {content}"
    _inject_channel_triggers(
        agent=agent,
        subscriber_id=creature_cfg.name,
        channel_names=all_listen,
        prompts=creature_prompts,
        ignore_sender=creature_cfg.name,
        registry=environment.shared_channels,
        config=config,
    )

    # -- Inject channel topology into the system prompt --
    topology_prompt = build_channel_topology_prompt(config, creature_cfg)
    if topology_prompt:
        inject_prompt_section(agent, topology_prompt)

    # -- Output log capture --
    capture: OutputLogCapture | None = None
    if creature_cfg.output_log:
        capture = OutputLogCapture(
            agent.output_router.default_output,
            max_entries=creature_cfg.output_log_size,
        )
        agent.output_router.default_output = capture
        logger.debug("Output log attached", creature=creature_cfg.name)

    return CreatureHandle(
        name=creature_cfg.name,
        agent=agent,
        config=creature_cfg,
        listen_channels=list(creature_cfg.listen_channels),
        send_channels=list(creature_cfg.send_channels),
        output_log=capture,
    )


def inject_prompt_section(agent: Agent, section: str) -> None:
    """
    Append *section* to the system message already stored in the
    agent's controller conversation.

    The controller sets up the system message during ``__init__``,
    so by the time we get here it is the first message in the list.
    """
    sys_msg = agent.controller.conversation.get_system_message()
    if sys_msg is None:
        return

    if isinstance(sys_msg.content, str):
        sys_msg.content = sys_msg.content + "\n\n" + section
    # If somehow multimodal, leave as-is (unlikely for system prompt)
