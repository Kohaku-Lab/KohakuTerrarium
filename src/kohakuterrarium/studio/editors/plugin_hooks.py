"""Plugin hook catalog shared by Studio routes and code generation.

Extracted so that ``studio.editors.codegen_plugin`` can import it
without a function-local import that would create a routes -> codegen
-> routes cycle.
"""

PLUGIN_HOOKS: list[dict] = [
    {
        "name": "on_load",
        "group": "lifecycle",
        "args_signature": ", context: PluginContext",
        "return_hint": " -> None",
        "description": "Called once when the plugin is loaded.",
    },
    {
        "name": "on_unload",
        "group": "lifecycle",
        "args_signature": "",
        "return_hint": " -> None",
        "description": "Called when the agent shuts down.",
    },
    {
        "name": "on_agent_start",
        "group": "lifecycle",
        "args_signature": "",
        "return_hint": " -> None",
        "description": "Called after agent.start() completes.",
    },
    {
        "name": "on_agent_stop",
        "group": "lifecycle",
        "args_signature": "",
        "return_hint": " -> None",
        "description": "Called before agent.stop() begins.",
    },
    {
        "name": "pre_llm_call",
        "group": "llm",
        "args_signature": ", messages: list[dict], **kwargs",
        "return_hint": " -> list[dict] | None",
        "description": "Before an LLM call. Return modified messages or None.",
    },
    {
        "name": "post_llm_call",
        "group": "llm",
        "args_signature": ", messages: list[dict], response: str, usage: dict, **kwargs",
        "return_hint": " -> None",
        "description": "After LLM call. Observation only.",
    },
    {
        "name": "pre_tool_execute",
        "group": "tool",
        "args_signature": ", args: dict, **kwargs",
        "return_hint": " -> dict | None",
        "description": (
            "Before tool run. Return modified args, or raise PluginBlockError."
        ),
    },
    {
        "name": "post_tool_execute",
        "group": "tool",
        "args_signature": ", result, **kwargs",
        "return_hint": " -> object | None",
        "description": "After tool run. Return modified result or None.",
    },
    {
        "name": "pre_subagent_run",
        "group": "subagent",
        "args_signature": ", task: str, **kwargs",
        "return_hint": " -> str | None",
        "description": (
            "Before sub-agent run. Return modified task or raise PluginBlockError."
        ),
    },
    {
        "name": "post_subagent_run",
        "group": "subagent",
        "args_signature": ", result, **kwargs",
        "return_hint": " -> object | None",
        "description": "After sub-agent run.",
    },
    {
        "name": "on_event",
        "group": "event",
        "args_signature": ", event",
        "return_hint": " -> None",
        "description": "Incoming trigger event. Observation only.",
    },
    {
        "name": "on_interrupt",
        "group": "event",
        "args_signature": "",
        "return_hint": " -> None",
        "description": "User interrupt fired.",
    },
    {
        "name": "on_task_promoted",
        "group": "event",
        "args_signature": ", job_id: str, tool_name: str",
        "return_hint": " -> None",
        "description": "A direct task was promoted to background.",
    },
    {
        "name": "on_compact_start",
        "group": "event",
        "args_signature": ", context_length: int",
        "return_hint": " -> bool | None",
        "description": (
            "Context compaction about to start. Return False to veto this "
            "cycle; any other return value (None, True) proceeds."
        ),
    },
    {
        "name": "on_compact_end",
        "group": "event",
        "args_signature": ", summary: str, messages_removed: int",
        "return_hint": " -> None",
        "description": ("Context compaction completed (not called when vetoed)."),
    },
]
