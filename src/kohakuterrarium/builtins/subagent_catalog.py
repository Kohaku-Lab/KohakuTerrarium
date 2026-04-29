"""
Builtin sub-agent catalog: pure lookup and registration.

Leaf module providing sub-agent configuration lookup without
package-level side effects. Internal code should import from here.
"""

import copy

from kohakuterrarium.builtins.subagents.coordinator import COORDINATOR_CONFIG
from kohakuterrarium.builtins.subagents.critic import CRITIC_CONFIG
from kohakuterrarium.builtins.subagents.explore import EXPLORE_CONFIG
from kohakuterrarium.builtins.subagents.memory_read import MEMORY_READ_CONFIG
from kohakuterrarium.builtins.subagents.memory_write import MEMORY_WRITE_CONFIG
from kohakuterrarium.builtins.subagents.plan import PLAN_CONFIG
from kohakuterrarium.builtins.subagents.research import RESEARCH_CONFIG
from kohakuterrarium.builtins.subagents.response import RESPONSE_CONFIG
from kohakuterrarium.builtins.subagents.summarize import SUMMARIZE_CONFIG
from kohakuterrarium.builtins.subagents.worker import WORKER_CONFIG
from kohakuterrarium.modules.subagent.config import SubAgentConfig

# All builtin sub-agent configurations
_BUILTIN_CONFIGS: dict[str, SubAgentConfig] = {
    "coordinator": COORDINATOR_CONFIG,
    "critic": CRITIC_CONFIG,
    "explore": EXPLORE_CONFIG,
    "plan": PLAN_CONFIG,
    "research": RESEARCH_CONFIG,
    "memory_read": MEMORY_READ_CONFIG,
    "memory_write": MEMORY_WRITE_CONFIG,
    "response": RESPONSE_CONFIG,
    "summarize": SUMMARIZE_CONFIG,
    "worker": WORKER_CONFIG,
}

BUILTIN_SUBAGENTS = list(_BUILTIN_CONFIGS.keys())


def get_builtin_subagent_config(name: str) -> SubAgentConfig | None:
    """Get a builtin sub-agent configuration by name.

    Returns a defensive copy so per-agent inline overrides never mutate the
    catalog singleton used by later agents or tests.
    """
    config = _BUILTIN_CONFIGS.get(name)
    return copy.deepcopy(config) if config is not None else None


def list_builtin_subagents() -> list[str]:
    """List all available builtin sub-agent names."""
    return BUILTIN_SUBAGENTS.copy()
