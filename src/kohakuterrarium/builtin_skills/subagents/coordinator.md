---
name: coordinator
description: Multi-agent orchestration sub-agent
category: subagent
tags: [coordination, orchestration, multi-agent, channels]
---

# coordinator

Sub-agent that coordinates multiple specialist agents to complete complex tasks.

## WHEN TO USE

- A task requires work from multiple specialist agents
- Subtasks have dependencies that need sequencing
- Results from different agents must be combined into a single outcome
- You need parallel execution across several channels

## WHEN NOT TO USE

- The task is simple enough for a single agent
- No inter-agent coordination is needed
- You just need to dispatch one message (use send_message directly)

## HOW TO USE

```
tool call: coordinator(
task description
)
```

## Arguments

| Arg  | Type    | Description                                                                                   |
| ---- | ------- | --------------------------------------------------------------------------------------------- |
| body | content | Full task description including what needs to be done and which channels/agents are available |

## Examples

```
tool call: coordinator(
Refactor the auth module: have the explore agent find all usages, the plan agent design the new API, then the worker agent implement the changes.
Channels: explore, plan, worker
)
```

```
tool call: coordinator(
Investigate and fix the failing test suite. Use explore to find the broken tests, critic to analyze root cause, and worker to apply the fix.
Channels: explore, critic, worker
)
```

```
tool call: coordinator(
Build a new REST endpoint for /users. Dispatch plan to design the schema, worker to implement, and critic to review the result.
Channels: plan, worker, critic
)
```

## CAPABILITIES

The coordinator sub-agent has access to:

- `send_message` - Dispatch subtasks to other agent channels
- `wait_channel` - Wait for results from agent channels
- `scratchpad` - Track dispatched tasks and intermediate state

It will autonomously:

1. Break the task into subtasks
2. Dispatch subtasks to the appropriate channels
3. Wait for and collect results
4. Re-dispatch on failure if recovery is possible
5. Synthesize a final combined result

## OUTPUT

Returns a structured summary including:

- Task breakdown showing subtask-to-channel mapping
- Individual results from each subtask
- Final synthesized outcome combining all results

## LIMITATIONS

- Cannot do work itself - relies entirely on other agents via channels
- Read-only (cannot modify files directly)
- Channels must already be configured and available
- Subject to timeout (600s default) across all subtask round-trips
- Maximum 20 turns for dispatching and collecting results

## TIPS

- List available channels explicitly in the task description
- Describe dependencies between subtasks so the coordinator sequences them correctly
- For simple two-agent workflows, consider using send_message + wait_channel directly
