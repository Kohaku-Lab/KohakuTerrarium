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
    """
    Builder for creating test agents with injected fakes.

    Constructs a lightweight agent setup (Controller + Executor + OutputRouter)
    without requiring a full Agent instance or config files.

    Usage:
        builder = TestAgentBuilder()
        builder.with_llm_script(["Hello!", "[/bash]echo hi[bash/]", "Done."])
        builder.with_builtin_tools(["bash", "read"])

        env = builder.build()

        # Run a turn
        await env.inject("User request")

        # Assert
        assert "Hello" in env.output.all_text
        assert env.llm.call_count == 1

    NOTE: ``__test__ = False`` opts this class out of pytest's
    ``Test*`` auto-collection.  The name predates pytest's
    convention; rather than rename and break every existing
    import site, we tell pytest to skip it.
    """

    __test__ = False

    def __init__(self):
        self._llm: ScriptedLLM | None = None
        self._output: OutputRecorder | None = None
        self._system_prompt: str = "You are a test agent."
        self._session_key: str = "test"
        self._tools: list[str] = []
        self._custom_tools: list[Any] = []  # Tool instances
        self._known_outputs: set[str] = set()
        self._named_outputs: dict[str, OutputModule] = {}
        self._ephemeral: bool = False

    def with_llm_script(
        self,
        script: list[ScriptEntry] | list[str],
    ) -> "TestAgentBuilder":
        """Set the LLM script."""
        self._llm = ScriptedLLM(script)
        return self

    def with_llm(self, llm: ScriptedLLM) -> "TestAgentBuilder":
        """Set a pre-configured LLM."""
        self._llm = llm
        return self

    def with_output(self, output: OutputRecorder) -> "TestAgentBuilder":
        """Set a custom output recorder."""
        self._output = output
        return self

    def with_system_prompt(self, prompt: str) -> "TestAgentBuilder":
        """Set system prompt."""
        self._system_prompt = prompt
        return self

    def with_session(self, key: str) -> "TestAgentBuilder":
        """Set session key (for shared channels)."""
        self._session_key = key
        return self

    def with_builtin_tools(self, tool_names: list[str]) -> "TestAgentBuilder":
        """Register builtin tools by name."""
        self._tools = tool_names
        return self

    def with_tool(self, tool: Any) -> "TestAgentBuilder":
        """Register a custom tool instance."""
        self._custom_tools.append(tool)
        return self

    def with_named_output(
        self,
        name: str,
        output: OutputModule,
    ) -> "TestAgentBuilder":
        """Add a named output module."""
        self._named_outputs[name] = output
        self._known_outputs.add(name)
        return self

    def with_ephemeral(self, ephemeral: bool = True) -> "TestAgentBuilder":
        """Set ephemeral mode."""
        self._ephemeral = ephemeral
        return self

    def build(self) -> "TestAgentEnv":
        """Build the test environment."""
        llm = self._llm or ScriptedLLM(["OK"])
        output = self._output or OutputRecorder()

        # Create session
        session = Session(key=self._session_key)
        set_session(session, key=self._session_key)

        # Create registry and register tools
        registry = Registry()

        # Register builtin tools if requested
        if self._tools:
            for name in self._tools:
                tool = get_builtin_tool(name)
                if tool:
                    registry.register_tool(tool)

        # Register custom tools
        for tool in self._custom_tools:
            registry.register_tool(tool)

        # Create executor
        executor = Executor()

        # Mirror tool registrations into executor
        for tool_name in registry.list_tools():
            tool_instance = registry.get_tool(tool_name)
            if tool_instance:
                executor.register_tool(tool_instance)

        # Set agent context on executor (direct attribute access, matches agent_init.py)
        executor._agent_name = "test_agent"
        executor._session = session
        executor._working_dir = Path.cwd()

        # Create controller
        config = ControllerConfig(
            system_prompt=self._system_prompt,
            known_outputs=self._known_outputs,
            ephemeral=self._ephemeral,
        )
        controller = Controller(llm, config, executor=executor, registry=registry)

        # Create output router
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
    """
    Test environment with all agent components wired together.

    Provides convenient methods for injecting input and collecting output.
    """

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
        """
        Inject user input and run one controller turn with output routing.

        This simulates the core of Agent._process_event_with_controller()
        but without the full agent lifecycle (triggers, termination, etc.).
        """
        event = create_user_input_event(text, source=source)
        await self.controller.push_event(event)

        await self.router.on_processing_start()

        async for parse_event in self.controller.run_once():
            if isinstance(parse_event, ToolCallEvent):
                # Start tool via executor
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
        """Inject a raw TriggerEvent."""
        await self.controller.push_event(event)

        async for parse_event in self.controller.run_once():
            await self.router.route(parse_event)

        await self.router.flush()
