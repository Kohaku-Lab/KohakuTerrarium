# builtin_skills/

Default documentation files for builtin tools and sub-agents, shipped with
the framework. Each markdown file provides full usage documentation that is
loaded on demand via the `##info##` command or the `info` tool. Users can
override any file by placing a same-named file in their agent's
`prompts/tools/` or `prompts/subagents/` folder.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Lookup functions: `get_builtin_tool_doc`, `get_builtin_subagent_doc`, listing and batch retrieval |
| `tools/*.md` | One markdown doc per builtin tool (bash, read, edit, write, glob, grep, etc.) |
| `subagents/*.md` | One markdown doc per builtin sub-agent (explore, plan, worker, critic, etc.) |

## Tool Docs (15)

bash, read, write, edit, glob, grep, tree, scratchpad,
ask_user, http, json_read, json_write, python, send_message, wait_channel

## Sub-agent Docs (10)

explore, plan, worker, critic, summarize, research, coordinator,
memory_read, memory_write, response

## Dependencies

None (standalone, uses only `pathlib`).
