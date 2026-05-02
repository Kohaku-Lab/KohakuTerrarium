# testing/

Reusable test infrastructure for KohakuTerrarium. Provides fake/mock
primitives for testing the agent framework without real LLMs or external
services. `ScriptedLLM` plays back predetermined responses with configurable
streaming simulation. `TestAgentBuilder` constructs lightweight agent setups
(Controller + Executor + OutputRouter) without config files. `OutputRecorder`
and `EventRecorder` capture all output and events for test assertions.

## Files

| File          | Description                                                                                    |
| ------------- | ---------------------------------------------------------------------------------------------- |
| `__init__.py` | Re-exports all test primitives                                                                 |
| `llm.py`      | `ScriptedLLM`, `ScriptEntry`: deterministic LLM that follows a script of predefined responses  |
| `agent.py`    | `TestAgentBuilder`: builder for creating test agents with injected fakes and builtin tools     |
| `output.py`   | `OutputRecorder`: captures writes, streaming chunks, and activity notifications for assertions |
| `events.py`   | `EventRecorder`, `RecordedEvent`: records events with timing and source information            |

## Dependencies

- `kohakuterrarium.builtins.tool_catalog` (get_builtin_tool)
- `kohakuterrarium.core.controller` (Controller, ControllerConfig)
- `kohakuterrarium.core.events` (TriggerEvent)
- `kohakuterrarium.core.executor` (Executor)
- `kohakuterrarium.core.registry` (Registry)
- `kohakuterrarium.core.session` (Session)
- `kohakuterrarium.llm.base` (LLMProvider, ChatResponse)
- `kohakuterrarium.llm.message` (Message)
- `kohakuterrarium.modules.output.base` (BaseOutputModule)
- `kohakuterrarium.modules.output.router` (OutputRouter)
- `kohakuterrarium.parsing` (ToolCallEvent, CommandResultEvent)
