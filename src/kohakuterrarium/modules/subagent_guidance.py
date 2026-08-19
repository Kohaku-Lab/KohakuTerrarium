"""Shared guidance for one-shot task-subagent invocations."""

TASK_SUBAGENT_CONTEXT_GUIDANCE = (
    "Each task-subagent call is a fresh, context-isolated invocation. It cannot "
    "resume or inherit conversation history from previous calls. Provide a "
    "complete, self-contained task that includes the original goal, current "
    "state, work already completed, what remains, and any relevant paths, "
    "errors, or findings. Never use shorthand such as 'continue the previous task'."
)
