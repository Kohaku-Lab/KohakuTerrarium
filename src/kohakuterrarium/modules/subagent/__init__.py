"""
Sub-agent system - nested agents with limited capabilities.

Sub-agents are full agents that:
- Have their own controller and conversation
- Limited tool access (configurable)
- Return results to parent controller (or output externally)
- Run as background jobs

Interactive sub-agents additionally:
- Stay alive continuously
- Receive context updates from parent
- Handle updates based on context_mode (interrupt, queue, flush)

Usage:
    from kohakuterrarium.modules.subagent import (
        SubAgent,
        SubAgentConfig,
        SubAgentManager,
        SubAgentResult,
        ContextUpdateMode,
        InteractiveSubAgent,
    )

    # Configure regular sub-agent
    config = SubAgentConfig(
        name="explore",
        description="Search codebase",
        tools=["glob", "grep", "read"],
        can_modify=False,
    )

    # Configure interactive sub-agent
    output_config = SubAgentConfig(
        name="output",
        description="Generate responses",
        interactive=True,
        context_mode=ContextUpdateMode.INTERRUPT_RESTART,
        output_to=OutputTarget.EXTERNAL,
    )

    # Use manager for spawning
    manager = SubAgentManager(registry, llm)
    manager.register(config)
    manager.register(output_config)

    # Spawn regular sub-agent
    job_id = await manager.spawn("explore", "Find auth code")
    result = await manager.wait_for(job_id)

    # Start interactive sub-agent
    agent = await manager.start_interactive("output", on_output=print)
    await manager.push_context("output", {"input": "Hello!"})
    await manager.stop_interactive("output")
"""

from kohakuterrarium.modules.subagent.base import SubAgent, SubAgentJob, SubAgentResult
from kohakuterrarium.modules.subagent.config import (
    ContextUpdateMode,
    OutputTarget,
    SubAgentConfig,
    SubAgentInfo,
)
from kohakuterrarium.modules.subagent.interactive import (
    ContextUpdate,
    InteractiveOutput,
    InteractiveSubAgent,
)
from kohakuterrarium.modules.subagent.manager import SubAgentManager

__all__ = [
    # Config
    "ContextUpdateMode",
    "OutputTarget",
    "SubAgentConfig",
    "SubAgentInfo",
    # Regular sub-agent
    "SubAgent",
    "SubAgentJob",
    "SubAgentResult",
    # Interactive sub-agent
    "ContextUpdate",
    "InteractiveOutput",
    "InteractiveSubAgent",
    # Manager
    "SubAgentManager",
]
