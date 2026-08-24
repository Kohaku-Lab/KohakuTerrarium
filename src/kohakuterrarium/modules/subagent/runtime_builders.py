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
from kohakuterrarium.modules.subagent.model_resolve import (  # noqa: F401
    resolve_subagent_llm as resolve_llm,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def build_plugin_manager(
    config: SubAgentConfig,
    loader: ModuleLoader,
    default_plugin_specs: list[dict[str, Any]],
):
    """Build a per-run manager from inline, catalog, and inherited plugins."""
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
    """Load plugins without rebinding tools shared with the parent registry."""
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
            target=float(data.get("target", CompactConfig.target)),
            keep_recent_turns=int(data.get("keep_recent_turns", 4)),
            enabled=bool(data.get("enabled", True)),
            cooldown_seconds=float(
                data.get("cooldown", data.get("cooldown_seconds", 20.0))
            ),
            compact_model=data.get("compact_model"),
        )
    )
    cm._llm = llm
    cm._agent_name = config.name
    return cm
