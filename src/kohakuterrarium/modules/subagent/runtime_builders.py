"""Runtime builder helpers for SubAgentManager."""

from pathlib import Path
from typing import Any

from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.subagent.base import SubAgent
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.model_resolve import (  # noqa: F401 — re-export
    resolve_subagent_llm as resolve_llm,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def build_plugin_manager(
    config: SubAgentConfig,
    loader: ModuleLoader,
    default_plugin_specs: list[dict[str, Any]],
):
    """Create the per-run PluginManager for a sub-agent.

    Combines:
      * inline ``plugins:`` entries from the sub-agent config (each with
        its own ``options`` — this is where the ``budget`` plugin gets
        its turn / walltime / tool_call axes)
      * ``default_plugins:`` pack names resolved against the catalog
      * ``default_plugin_specs`` propagated from the parent (used when a
        terrarium / parent agent wants to seed every sub-agent with a
        common plugin)
    """
    return init_plugins(
        list(getattr(config, "plugins", []) or []),
        loader,
        default_plugins=config.default_plugins,
        default_plugin_specs=default_plugin_specs,
    )


async def load_and_wrap_plugins(
    plugin_manager: Any,
    subagent: SubAgent,
    llm: LLMProvider,
    agent_path: Path | None,
) -> None:
    """Load sub-agent plugins.

    The sub-agent's tool dispatcher (``SubAgent._execute_tools``) fires
    ``pre_tool_execute`` / ``post_tool_execute`` at the call site, so
    no per-tool wiring is needed here. We must NOT rebind
    ``tool.execute`` — tool instances are shared with the parent's
    registry, and a rebind would leak the sub-agent's plugin chain
    onto every parent tool call (e.g. a sub-agent's exhausted budget
    blocking the parent's bash).
    """
    if not plugin_manager:
        return
    ctx = PluginContext(
        agent_name=subagent.config.name,
        working_dir=agent_path or Path.cwd(),
        model=getattr(llm, "model", getattr(getattr(llm, "config", None), "model", "")),
        _host_agent=subagent,
    )
    await plugin_manager.load_all(ctx)


def build_compact_manager(
    config: SubAgentConfig, llm: LLMProvider
) -> CompactManager | None:
    """Create a CompactManager for sub-agents that opt into compaction."""
    if not config.compact:
        return None
    data = config.compact
    default_max = getattr(llm, "_profile_max_context", CompactConfig.max_tokens)
    cm = CompactManager(
        CompactConfig(
            max_tokens=int(data.get("max_tokens") or default_max),
            threshold=float(data.get("threshold", 0.75)),
            target=float(data.get("target", 0.40)),
            keep_recent_turns=int(data.get("keep_recent_turns", 4)),
            cooldown_seconds=float(
                data.get("cooldown", data.get("cooldown_seconds", 20.0))
            ),
            compact_model=data.get("compact_model"),
        )
    )
    cm._llm = llm
    cm._agent_name = config.name
    return cm
