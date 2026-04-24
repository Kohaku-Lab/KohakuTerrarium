# builtin_skills/

Default documentation files for builtin tools and sub-agents, shipped with
the framework. Each markdown file provides full usage documentation that is
loaded on demand via the `info` tool. Users can
override any file by placing a same-named file in their agent's
`prompts/tools/` or `prompts/subagents/` folder.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Lookup functions: `get_builtin_tool_doc`, `get_builtin_subagent_doc`, listing and batch retrieval |
| `tools/*.md` | One markdown doc per builtin tool (bash, read, edit, write, glob, grep, etc.) |
| `subagents/*.md` | One markdown doc per builtin sub-agent (explore, plan, worker, critic, etc.) |

## Tool Docs (25)

ask_user, bash, edit, glob, grep, image_gen, info, json_read, json_write,
mcp_call, mcp_connect, mcp_disconnect, mcp_list, multi_edit, python, read,
scratchpad, search_memory, send_message, skill, stop_task, tree, web_fetch,
web_search, write

## Sub-agent Docs (10)

coordinator, critic, explore, memory_read, memory_write, plan, research,
response, summarize, worker

## Dependencies

None (standalone, uses only `pathlib`).
