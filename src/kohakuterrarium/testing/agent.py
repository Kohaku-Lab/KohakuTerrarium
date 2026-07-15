"""Test agent builder for constructing agents with test doubles."""

from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.session import Session, set_session
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.router import OutputRouter
from kohakuterrarium.parsing import CommandResultEvent, ToolCallEvent
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry
from kohakuterrarium.testing.output import OutputRecorder


class TestAgentBuilder:
    """Build lightweight controller, executor, registry, and output test setups.

    ``__test__ = False`` preserves the public ``Test*`` name without pytest
    collecting this helper as a test class.
    """

    __test__ = False

    def __init__(self):
        self._llm: ScriptedLLM | None = None
        self._output: OutputRecorder | None = None
        self._system_prompt: str = "You are a test agent."
        self._session_key: str = "test"
        self._tools: list[str] = []
        self._custom_tools: list[Any] = []
        self._known_outputs: set[str] = set()
        self._named_outputs: dict[str, OutputModule] = {}
        self._ephemeral: bool = False

    def with_llm_script(
        self,
        script: list[ScriptEntry] | list[str],
    ) -> "TestAgentBuilder":
        """Configure a new scripted LLM from ordered responses."""
        self._llm = ScriptedLLM(script)
        return self

    def with_llm(self, llm: ScriptedLLM) -> "TestAgentBuilder":
        """Use a preconfigured scripted LLM."""
        self._llm = llm
        return self

    def with_output(self, output: OutputRecorder) -> "TestAgentBuilder":
        """Use a custom output recorder."""
        self._output = output
        return self

    def with_system_prompt(self, prompt: str) -> "TestAgentBuilder":
        """Set the controller system prompt."""
        self._system_prompt = prompt
        return self

    def with_session(self, key: str) -> "TestAgentBuilder":
        """Set the session key used by shared test channels."""
        self._session_key = key
        return self

    def with_builtin_tools(self, tool_names: list[str]) -> "TestAgentBuilder":
        """Select built-in tools to register."""
        self._tools = tool_names
        return self

    def with_tool(self, tool: Any) -> "TestAgentBuilder":
        """Add a custom tool instance."""
        self._custom_tools.append(tool)
        return self

    def with_named_output(
        self,
        name: str,
        output: OutputModule,
    ) -> "TestAgentBuilder":
        """Register a named output module and expose its target."""
        self._named_outputs[name] = output
        self._known_outputs.add(name)
        return self

    def with_ephemeral(self, ephemeral: bool = True) -> "TestAgentBuilder":
        """Configure ephemeral controller history."""
        self._ephemeral = ephemeral
        return self

    def build(self) -> "TestAgentEnv":
        """Construct and wire the configured test environment."""
        llm = self._llm or ScriptedLLM(["OK"])
        output = self._output or OutputRecorder()

        session = Session(key=self._session_key)
        set_session(session, key=self._session_key)

        registry = Registry()

        if self._tools:
            for name in self._tools:
                tool = get_builtin_tool(name)
                if tool:
                    registry.register_tool(tool)

        for tool in self._custom_tools:
            registry.register_tool(tool)

        executor = Executor()

        # Registry and executor must expose the same tool instances.
        for tool_name in registry.list_tools():
            tool_instance = registry.get_tool(tool_name)
            if tool_instance:
                executor.register_tool(tool_instance)

        # Mirror production bootstrap's executor context contract.
        executor._agent_name = "test_agent"
        executor._session = session
        executor._working_dir = Path.cwd()

        config = ControllerConfig(
            system_prompt=self._system_prompt,
            known_outputs=self._known_outputs,
            ephemeral=self._ephemeral,
        )
        controller = Controller(llm, config, executor=executor, registry=registry)

        router = OutputRouter(
            default_output=output,
            named_outputs=self._named_outputs,
        )

        return TestAgentEnv(
            llm=llm,
            output=output,
            controller=controller,
            executor=executor,
            registry=registry,
            router=router,
            session=session,
        )


class TestAgentEnv:
    """Expose a wired test runtime for input injection and output inspection."""

    __test__ = False

    def __init__(
        self,
        llm: ScriptedLLM,
        output: OutputRecorder,
        controller: Controller,
        executor: Executor,
        registry: Registry,
        router: OutputRouter,
        session: Session,
    ):
        self.llm = llm
        self.output = output
        self.controller = controller
        self.executor = executor
        self.registry = registry
        self.router = router
        self.session = session

    async def inject(self, text: str, source: str = "test") -> None:
        """Run one user-input turn without the full agent lifecycle."""
        event = create_user_input_event(text, source=source)
        await self.controller.push_event(event)

        await self.router.on_processing_start()

        async for parse_event in self.controller.run_once():
            if isinstance(parse_event, ToolCallEvent):
                job_id = await self.executor.submit_from_event(parse_event)
                self.output.on_activity(
                    "tool_start",
                    f"[{parse_event.name}] {job_id}",
                )
            elif isinstance(parse_event, CommandResultEvent):
                if parse_event.error:
                    self.output.on_activity(
                        "command_error",
                        f"[{parse_event.command}] {parse_event.error}",
                    )
                else:
                    self.output.on_activity(
                        "command_done",
                        f"[{parse_event.command}] OK",
                    )
            else:
                await self.router.route(parse_event)

        await self.router.flush()
        await self.router.on_processing_end()

    async def inject_event(self, event: TriggerEvent) -> None:
        """Run one turn from a prebuilt trigger event."""
        await self.controller.push_event(event)

        async for parse_event in self.controller.run_once():
            await self.router.route(parse_event)

        await self.router.flush()
