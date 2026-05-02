# builtins/subagents/

Built-in sub-agent configurations. Each file defines a `SubAgentConfig`
constant (e.g., `EXPLORE_CONFIG`) that specifies the sub-agent's name,
description, allowed tools, output routing, and execution limits. The
`__init__.py` re-exports from `builtins.subagent_catalog` for convenience
and backward compatibility.

## Files

| File              | Description                                                          |
| ----------------- | -------------------------------------------------------------------- |
| `__init__.py`     | Re-exports from `subagent_catalog`, imports all config constants     |
| `explore.py`      | `EXPLORE_CONFIG`: search and explore codebase (read-only)            |
| `plan.py`         | `PLAN_CONFIG`: create implementation plans (read-only)               |
| `worker.py`       | `WORKER_CONFIG`: implement code changes, fix bugs (read-write)       |
| `critic.py`       | `CRITIC_CONFIG`: review and critique code or plans                   |
| `summarize.py`    | `SUMMARIZE_CONFIG`: summarize long content                           |
| `research.py`     | `RESEARCH_CONFIG`: research topics using files and web               |
| `coordinator.py`  | `COORDINATOR_CONFIG`: coordinate multiple agents via channels        |
| `memory_read.py`  | `MEMORY_READ_CONFIG`: search and retrieve from memory                |
| `memory_write.py` | `MEMORY_WRITE_CONFIG`: store information to memory                   |
| `response.py`     | `RESPONSE_CONFIG`: generate user-facing responses (output sub-agent) |

## Dependencies

- `kohakuterrarium.builtins.subagent_catalog` (registry and lookup)
- `kohakuterrarium.modules.subagent.config` (SubAgentConfig, OutputTarget)
