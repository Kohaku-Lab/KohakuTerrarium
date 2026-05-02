# modules/subagent/

Sub-agent lifecycle management. Sub-agents are nested agents with their own
controller, conversation, and limited tool access. They run as background jobs
and return results to the parent controller (or stream output externally).
Interactive sub-agents stay alive continuously and receive context updates
from the parent, handling them based on a configurable mode (interrupt/restart,
queue/append, flush/replace).

## Files

| File             | Description                                                                     |
| ---------------- | ------------------------------------------------------------------------------- |
| `__init__.py`    | Re-exports all sub-agent classes and enums                                      |
| `config.py`      | `SubAgentConfig`, `SubAgentInfo`, `OutputTarget` enum, `ContextUpdateMode` enum |
| `base.py`        | `SubAgent` (single-run execution), `SubAgentJob`, `SubAgentResult`              |
| `interactive.py` | `InteractiveSubAgent` (long-lived), `ContextUpdate`, `InteractiveOutput`        |
| `manager.py`     | `SubAgentManager`: spawn, track, wait for, and stop sub-agents                  |

## Dependencies

- `kohakuterrarium.core.controller` (Controller)
- `kohakuterrarium.core.conversation` (Conversation)
- `kohakuterrarium.core.events` (TriggerEvent)
- `kohakuterrarium.core.executor` (Executor)
- `kohakuterrarium.core.job` (JobStore, JobResult, JobStatus)
- `kohakuterrarium.core.registry` (Registry)
- `kohakuterrarium.llm.base` (LLMProvider)
- `kohakuterrarium.llm.tools` (build_tool_schemas)
- `kohakuterrarium.utils.logging`
