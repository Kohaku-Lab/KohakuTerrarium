"""Runtime builder helpers for SubAgentManager."""

from pathlib import Path
from typing import Any

from kohakuterrarium.bootstrap.plugins import init_plugins
from kohakuterrarium.core.budget import BudgetAxis, BudgetSet, IterationBudget
from kohakuterrarium.core.compact import CompactConfig, CompactManager
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.llm.base import LLMProvider
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.subagent.base import SubAgent
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def resolve_llm(parent_llm: LLMProvider, config: SubAgentConfig) -> LLMProvider:
    """Return a sub-agent-specific provider, falling back to the parent."""
    if not config.model:
        return parent_llm
    try:
        return parent_llm.with_model(config.model)
    except Exception as exc:
        logger.warning(
            "Sub-agent model override failed; inheriting parent LLM",
            subagent_name=config.name,
            model=config.model,
            error=str(exc),
        )
        return parent_llm


def build_plugin_manager(
    config: SubAgentConfig,
    loader: ModuleLoader,
    default_plugin_specs: list[dict[str, Any]],
):
    """Create the per-run PluginManager for a sub-agent."""
    return init_plugins(
        [],
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
    """Load sub-agent plugins and wrap its tools."""
    if not plugin_manager:
        return
    ctx = PluginContext(
        agent_name=subagent.config.name,
        working_dir=agent_path or Path.cwd(),
        model=getattr(llm, "model", getattr(getattr(llm, "config", None), "model", "")),
        _host_agent=subagent,
    )
    await plugin_manager.load_all(ctx)
    for tool_name in subagent.registry.list_tools():
        tool = subagent.registry.get_tool(tool_name)
        if tool is not None and hasattr(tool, "execute"):
            tool.execute = plugin_manager.wrap_method(
                "pre_tool_execute",
                "post_tool_execute",
                tool.execute,
                input_kwarg="args",
                extra_kwargs={"tool_name": tool_name},
            )


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


def build_budgets(
    config: SubAgentConfig,
    parent_budgets: BudgetSet | None = None,
    legacy_budget: IterationBudget | None = None,
) -> BudgetSet | None:
    """Create or inherit per-sub-agent multi-axis budgets from config."""
    if _has_explicit_budget(config):
        return _configured_budget_set(config)
    if getattr(config, "budget_allocation", None) is not None:
        allocation = int(config.budget_allocation or 0)
        return BudgetSet(turn=BudgetAxis(name="turn", soft=0, hard=float(allocation)))
    if config.budget_inherit and parent_budgets is not None:
        return parent_budgets
    if config.budget_inherit and legacy_budget is not None:
        return legacy_budget.budgets
    return None


def _configured_budget_set(config: SubAgentConfig) -> BudgetSet | None:
    turn = _axis_from_tuple("turn", config.turn_budget)
    walltime = _axis_from_tuple("walltime", config.walltime_budget)
    tool_call = _axis_from_tuple("tool_call", config.tool_call_budget)
    if turn is None and walltime is None and tool_call is None:
        return None
    return BudgetSet(turn=turn, walltime=walltime, tool_call=tool_call)


def _has_explicit_budget(config: SubAgentConfig) -> bool:
    return any(
        value is not None
        for value in (
            config.turn_budget,
            config.walltime_budget,
            config.tool_call_budget,
        )
    )


def _axis_from_tuple(name: str, value: tuple[Any, Any] | None) -> BudgetAxis | None:
    if value is None:
        return None
    soft, hard = value
    return BudgetAxis(name=name, soft=float(soft), hard=float(hard))
