"""Interactive sub-agent management mixin."""

from typing import Any, Callable

from kohakuterrarium.modules.subagent.base import SubAgent
from kohakuterrarium.modules.subagent.interactive import (
    InteractiveOutput,
    InteractiveSubAgent,
)
from kohakuterrarium.modules.subagent.model_resolve import resolve_subagent_llm
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class InteractiveManagerMixin:
    """Manage persistent interactive sub-agents for a compatible host manager."""

    async def start_interactive(
        self,
        name: str,
        on_output: Callable[[InteractiveOutput], None] | None = None,
    ) -> InteractiveSubAgent:
        """Start a registered interactive sub-agent or return its live instance."""
        config = self._configs.get(name)
        if config is None:
            raise ValueError(f"Sub-agent not registered: {name}")

        if not config.interactive:
            raise ValueError(f"Sub-agent is not interactive: {name}")

        if name in self._interactive:
            logger.warning(
                "Interactive sub-agent already running",
                subagent_name=name,
            )
            return self._interactive[name]

        effective_tool_format = config.tool_format or self._tool_format

        # Interactive and task sub-agents must share model-resolution semantics.
        llm = resolve_subagent_llm(self.llm, config)

        agent = InteractiveSubAgent(
            config=config,
            parent_registry=self.parent_registry,
            llm=llm,
            agent_path=self.agent_path,
            tool_format=effective_tool_format,
        )

        if on_output:
            agent.on_output = on_output
            self._output_callbacks[name] = on_output

        await agent.start()
        self._interactive[name] = agent

        logger.info(
            "Started interactive sub-agent",
            subagent_name=name,
            context_mode=config.context_mode.value,
        )

        return agent

    async def stop_interactive(self, name: str) -> None:
        """Stop and unregister a running interactive sub-agent."""
        agent = self._interactive.get(name)
        if agent:
            await agent.stop()
            del self._interactive[name]
            self._output_callbacks.pop(name, None)

            logger.info("Stopped interactive sub-agent", subagent_name=name)

    async def stop_all_interactive(self) -> None:
        """Stop all running interactive sub-agents."""
        names = list(self._interactive.keys())
        for name in names:
            await self.stop_interactive(name)

    async def push_context(
        self,
        name: str,
        context: dict[str, Any],
    ) -> None:
        """Push context to a running interactive sub-agent."""
        agent = self._interactive.get(name)
        if agent is None:
            raise ValueError(f"Interactive sub-agent not running: {name}")

        await agent.push_context(context)

    async def push_context_all(self, context: dict[str, Any]) -> None:
        """Push context to every running interactive sub-agent."""
        for agent in self._interactive.values():
            await agent.push_context(context)

    def get_interactive(self, name: str) -> InteractiveSubAgent | None:
        """Return a running interactive sub-agent by name."""
        return self._interactive.get(name)

    def list_interactive(self) -> list[str]:
        """Return the names of running interactive sub-agents."""
        return list(self._interactive.keys())

    def get_live_subagent(self, job_id: str) -> SubAgent | None:
        """Return the live task sub-agent for a job, if it remains tracked."""
        job = self._jobs.get(job_id)
        return getattr(job, "subagent", None) if job is not None else None

    def get_subagent_conversation(self, job_id: str) -> list[dict[str, Any]] | None:
        """Return conversation messages for a currently tracked task sub-agent."""
        subagent = self.get_live_subagent(job_id)
        if subagent is None:
            return None
        conversation = getattr(subagent, "conversation", None)
        if conversation is None:
            return None
        return conversation.to_messages()

    async def send_to_subagent(
        self, content: str, *, job_id: str | None = None, name: str | None = None
    ) -> bool:
        """Deliver a message to an exact job or the most recent live named child."""
        if job_id:
            subagent = self.get_live_subagent(job_id)
            if subagent is not None and getattr(subagent, "is_running", False):
                subagent.push_message(content)
                return True
            return False
        if name:
            interactive = self._interactive.get(name)
            if interactive is not None and interactive.is_active:
                await interactive.push_context({"message": content})
                return True
            subagent = self._live_task_by_name(name)
            if subagent is not None:
                subagent.push_message(content)
                return True
        return False

    def _live_task_by_name(self, name: str) -> SubAgent | None:
        """The most-recent live task sub-agent registered under ``name``."""
        for job in reversed(list(self._jobs.values())):
            subagent = getattr(job, "subagent", None)
            if subagent is None:
                continue
            if getattr(subagent.config, "name", None) != name:
                continue
            if getattr(subagent, "is_running", False):
                return subagent
        return None

    def get_interactive_output(self, name: str) -> str:
        """Return and clear buffered output from an interactive sub-agent."""
        agent = self._interactive.get(name)
        if agent:
            return agent.get_buffered_output()
        return ""

    def set_output_callback(
        self,
        name: str,
        callback: Callable[[InteractiveOutput], None],
    ) -> None:
        """Set the output callback for a running interactive sub-agent."""
        agent = self._interactive.get(name)
        if agent:
            agent.on_output = callback
            self._output_callbacks[name] = callback
