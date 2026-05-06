"""Builtin tool parameter schemas.

Pure-data module — one entry per built-in tool, keyed by tool_name.
Used by llm/tools.py:build_tool_schemas when constructing the
native function-calling schema list passed to the LLM.

This file intentionally lives standalone and is exempt from the file-
size guard: every new builtin tool adds an entry here, and inlining
the dict in tools.py made the dispatch logic hard to read once the
catalogue grew past ~30 entries.

If a tool is missing from this dict, build_tool_schemas falls back
to a generic {content: string} schema — which silently strips
structured arguments. The regression test
tests/unit/test_tool_schemas_complete.py enforces every registered
builtin tool has an entry here.
"""

_BUILTIN_SCHEMAS: dict[str, dict] = {
    "bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "type": {
                "type": "string",
                "description": "Shell type (default: bash). Options: bash, zsh, sh, fish, pwsh",
            },
            "timeout": {
                "type": "number",
                "description": "Maximum execution time in seconds (0 = no timeout).",
            },
        },
        "required": ["command"],
    },
    "python": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute"},
            "timeout": {
                "type": "number",
                "description": "Maximum execution time in seconds (0 = no timeout).",
            },
        },
        "required": ["code"],
    },
    "read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "offset": {"type": "integer", "description": "Line offset (optional)"},
            "limit": {"type": "integer", "description": "Max lines (optional)"},
        },
        "required": ["path"],
    },
    "write": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "File content"},
        },
        "required": ["path", "content"],
    },
    "edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to edit"},
            "old": {
                "type": "string",
                "description": "Exact text to find (search/replace mode)",
            },
            "new": {
                "type": "string",
                "description": "Replacement text (search/replace mode)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false)",
            },
            "diff": {
                "type": "string",
                "description": "Unified diff content (diff mode, alternative to old/new)",
            },
        },
        "required": ["path"],
    },
    "multi_edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to edit"},
            "edits": {
                "type": "array",
                "description": "Ordered list of search/replace edits applied to one file",
                "items": {
                    "type": "object",
                    "properties": {
                        "old": {"type": "string", "description": "Exact text to find"},
                        "new": {"type": "string", "description": "Replacement text"},
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace all occurrences for this edit (default false)",
                        },
                    },
                    "required": ["old", "new"],
                },
            },
            "strict": {
                "type": "boolean",
                "description": "If true (default), any failed edit aborts the whole operation and leaves the file unchanged",
            },
            "best_effort": {
                "type": "boolean",
                "description": "If true, keep going after failed edits. Cannot be combined with strict=true",
            },
        },
        "required": ["path", "edits"],
    },
    "notebook_read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Jupyter .ipynb file path"},
            "cell_id": {
                "type": "string",
                "description": "Optional real cell id or synthetic cell-N reference",
            },
            "offset": {"type": "integer", "description": "Cell offset (optional)"},
            "limit": {"type": "integer", "description": "Max cells to read (optional)"},
            "include_outputs": {
                "type": "string",
                "enum": ["none", "summary", "all"],
                "description": "Output detail level (default summary)",
            },
            "include_metadata": {
                "type": "boolean",
                "description": "Include cell metadata (default false)",
            },
            "max_source_chars": {
                "type": "integer",
                "description": "Max source characters per cell (default 8000)",
            },
            "max_output_chars": {
                "type": "integer",
                "description": "Max output characters per output (default 4000)",
            },
        },
        "required": ["path"],
    },
    "notebook_edit": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Jupyter .ipynb file path"},
            "cell_id": {
                "type": "string",
                "description": "Cell id for single replace/delete or insertion anchor",
            },
            "new_source": {
                "type": "string",
                "description": "New source for single replace/insert edit",
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown", "raw"],
                "description": "Cell type for insert, or optional type conversion for replace",
            },
            "edit_mode": {
                "type": "string",
                "enum": ["replace", "insert", "delete"],
                "description": "Single edit mode (default replace)",
            },
            "insert_location": {
                "type": "string",
                "enum": ["after", "before", "beginning", "end"],
                "description": "Where to insert relative to cell_id (default after; end if no cell_id)",
            },
            "clear_outputs": {
                "type": "boolean",
                "description": "Clear outputs/execution count for code replacements (default true)",
            },
            "edits": {
                "type": "array",
                "description": "Ordered notebook cell edits; if present, replaces single-edit args",
                "items": {
                    "type": "object",
                    "properties": {
                        "cell_id": {
                            "type": "string",
                            "description": "Real cell id or synthetic cell-N reference",
                        },
                        "new_source": {
                            "type": "string",
                            "description": "New cell source",
                        },
                        "cell_type": {
                            "type": "string",
                            "enum": ["code", "markdown", "raw"],
                            "description": "Cell type for insert or optional conversion",
                        },
                        "edit_mode": {
                            "type": "string",
                            "enum": ["replace", "insert", "delete"],
                            "description": "replace, insert, or delete (default replace)",
                        },
                        "insert_location": {
                            "type": "string",
                            "enum": ["after", "before", "beginning", "end"],
                            "description": "Insertion location (default after)",
                        },
                        "clear_outputs": {
                            "type": "boolean",
                            "description": "Clear outputs for code replacements (default true)",
                        },
                    },
                },
            },
            "strict": {
                "type": "boolean",
                "description": "If true (default), any failed edit aborts and leaves file unchanged",
            },
            "best_effort": {
                "type": "boolean",
                "description": "If true, keep going after failed edits. Cannot be combined with strict=true",
            },
        },
        "required": ["path"],
    },
    "glob": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. **/*.py)"},
            "path": {"type": "string", "description": "Base directory (optional)"},
            "gitignore": {
                "type": "boolean",
                "description": "Follow .gitignore rules (default true)",
            },
        },
        "required": ["pattern"],
    },
    "grep": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search"},
            "path": {"type": "string", "description": "Directory or file to search"},
            "glob": {"type": "string", "description": "File glob filter (optional)"},
            "gitignore": {
                "type": "boolean",
                "description": "Follow .gitignore rules (default true)",
            },
        },
        "required": ["pattern"],
    },
    "tree": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path"},
            "depth": {"type": "integer", "description": "Max depth (default 3)"},
            "limit": {
                "type": "integer",
                "description": "Max output lines (default 100, 0 = unlimited)",
            },
            "gitignore": {
                "type": "boolean",
                "description": "Follow .gitignore rules (default true)",
            },
        },
    },
    "scratchpad": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "delete", "list"],
                "description": "Operation to perform",
            },
            "key": {"type": "string", "description": "Key name"},
            "value": {"type": "string", "description": "Value (for set)"},
        },
        "required": ["action"],
    },
    "send_message": {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "description": "Channel name"},
            "message": {"type": "string", "description": "Message content"},
            "reply_to": {"type": "string", "description": "Message ID to reply to"},
        },
        "required": ["channel", "message"],
    },
    "info": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the tool, sub-agent, or procedural skill to get documentation for",
            },
        },
        "required": ["name"],
    },
    "skill": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the procedural skill to invoke",
            },
            "arguments": {
                "type": "string",
                "description": "Optional user/task arguments to pass along to the skill",
            },
        },
        "required": ["name"],
    },
    "search_memory": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (keywords or natural language)",
            },
            "mode": {
                "type": "string",
                "enum": ["fts", "semantic", "hybrid", "auto"],
                "description": "Search mode (default: auto)",
            },
            "k": {
                "type": "integer",
                "description": "Max results (default: 5)",
            },
        },
        "required": ["query"],
    },
    "web_fetch": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch and read",
            },
        },
        "required": ["url"],
    },
    "web_search": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results (default: 10)",
            },
        },
        "required": ["query"],
    },
    "ask_user": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Question to ask the user"},
        },
        "required": ["question"],
    },
    "json_read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "JSON file path"},
            "query": {"type": "string", "description": "JMESPath query (optional)"},
        },
        "required": ["path"],
    },
    "json_write": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "JSON file path"},
            "content": {"type": "string", "description": "JSON content to write"},
        },
        "required": ["path", "content"],
    },
    "show_card": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Card header (required).",
            },
            "subtitle": {
                "type": "string",
                "description": "Optional smaller header line shown after the title.",
            },
            "icon": {
                "type": "string",
                "description": "Optional emoji shown before the title (e.g. '🔧').",
            },
            "accent": {
                "type": "string",
                "enum": ["primary", "info", "success", "warning", "danger", "neutral"],
                "description": (
                    "Semantic color used for border / header. Pick one that "
                    "matches the meaning: success for completion, warning for "
                    "review-needed, danger for destructive, info for neutral."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "Markdown body — supports headings, lists, code fences, "
                    "links. Keep it short; cards are for at-a-glance info."
                ),
            },
            "fields": {
                "type": "array",
                "description": (
                    "Optional key/value rows displayed as a small grid. "
                    "Set 'inline: true' to pack two rows side-by-side."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                        "inline": {"type": "boolean"},
                    },
                    "required": ["label", "value"],
                },
            },
            "footer": {
                "type": "string",
                "description": "Optional small italic line at the bottom.",
            },
            "actions": {
                "type": "array",
                "description": (
                    "Optional buttons. When present and 'wait_for_reply' is "
                    "true (the default), the tool blocks until the user clicks "
                    "one and returns 'action: <id>'. Use sparingly — 2 to 4 "
                    "actions max."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Stable id returned when the user clicks.",
                        },
                        "label": {"type": "string"},
                        "style": {
                            "type": "string",
                            "enum": ["primary", "secondary", "danger", "link"],
                            "description": (
                                "'link' actions open 'url' in a browser without "
                                "submitting a reply (no agent round-trip)."
                            ),
                        },
                        "url": {
                            "type": "string",
                            "description": "Required when style is 'link'.",
                        },
                    },
                    "required": ["id", "label"],
                },
            },
            "wait_for_reply": {
                "type": "boolean",
                "description": (
                    "Whether to block waiting for a button click. Defaults to "
                    "true when 'actions' is non-empty, false otherwise."
                ),
            },
            "timeout_s": {
                "type": "number",
                "description": (
                    "Seconds to wait for a reply. Default null = wait forever."
                ),
            },
            "surface": {
                "type": "string",
                "enum": ["chat", "modal"],
                "description": "Where to render the card. Default 'chat'.",
            },
        },
        "required": ["title"],
    },
    "stop_task": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "ID of the background job, tool, or sub-agent to cancel.",
            },
        },
        "required": ["job_id"],
    },
    "image_gen": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Text prompt describing the image to generate.",
            },
        },
        "required": ["prompt"],
    },
    "group_status": {
        "type": "object",
        "properties": {
            "include_history": {
                "type": "boolean",
                "description": "Include the last N channel messages (default false).",
            },
            "history_limit": {
                "type": "integer",
                "description": "Number of recent messages per channel (default 10).",
            },
            "include_spawnable": {
                "type": "boolean",
                "description": "Include catalog of creatures spawnable via group_add_node (default true).",
            },
        },
    },
    "group_add_node": {
        "type": "object",
        "properties": {
            "config_path": {
                "type": "string",
                "description": "Creature config path or @pkg/creatures/<name> reference.",
            },
            "name": {
                "type": "string",
                "description": "Optional display name override for the new creature.",
            },
            "llm": {
                "type": "string",
                "description": "Optional LLM profile override.",
            },
            "pwd": {
                "type": "string",
                "description": "Working directory; defaults to caller's pwd.",
            },
        },
        "required": ["config_path"],
    },
    "group_remove_node": {
        "type": "object",
        "properties": {
            "creature_id": {
                "type": "string",
                "description": "Creature id (or name) to destroy. Privileged creatures are protected.",
            },
        },
        "required": ["creature_id"],
    },
    "group_start_node": {
        "type": "object",
        "properties": {
            "creature_id": {
                "type": "string",
                "description": "Creature id (or name) to start.",
            },
        },
        "required": ["creature_id"],
    },
    "group_stop_node": {
        "type": "object",
        "properties": {
            "creature_id": {
                "type": "string",
                "description": "Creature id (or name) to stop.",
            },
        },
        "required": ["creature_id"],
    },
    "group_channel": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "delete", "wire", "unwire"],
            },
            "channel": {
                "type": "string",
                "description": "Channel name.",
            },
            "description": {
                "type": "string",
                "description": "Channel description (action='create' only).",
            },
            "creature_id": {
                "type": "string",
                "description": "Creature id (or name) to wire/unwire.",
            },
            "direction": {
                "type": "string",
                "enum": ["send", "listen"],
                "description": "Edge direction for wire/unwire.",
            },
        },
        "required": ["action", "channel"],
    },
    "group_wire": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "remove"]},
            "to": {
                "type": "string",
                "description": "Target creature for action='add'.",
            },
            "from": {
                "type": "string",
                "description": "Source creature; defaults to caller.",
            },
            "with_content": {
                "type": "boolean",
                "description": "Whether the wire delivers source content (default true).",
            },
            "prompt": {
                "type": "string",
                "description": "Optional receiver-side template.",
            },
            "prompt_format": {
                "type": "string",
                "enum": ["simple", "jinja"],
            },
            "allow_self_trigger": {
                "type": "boolean",
                "description": "Allow source==target self-trigger (default false).",
            },
            "edge_id": {
                "type": "string",
                "description": "Edge id (action='remove' only).",
            },
        },
        "required": ["action"],
    },
    "group_send": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Target creature in your group.",
            },
            "message": {
                "type": "string",
                "description": "Message content delivered to the target's controller.",
            },
        },
        "required": ["to", "message"],
    },
    "send_channel": {
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "Channel name (must be wired as outgoing for the caller).",
            },
            "message": {
                "type": "string",
                "description": "Message content broadcast to all listeners.",
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata bag.",
            },
            "reply_to": {
                "type": "string",
                "description": "Optional message id this is replying to.",
            },
        },
        "required": ["channel", "message"],
    },
    "mcp_list": {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": (
                    "Optional MCP server name. Omit to list tools from "
                    "every connected server."
                ),
            },
        },
    },
    "mcp_call": {
        "type": "object",
        "properties": {
            "server": {"type": "string", "description": "MCP server name."},
            "tool": {"type": "string", "description": "Tool name on the server."},
            "args": {
                "type": "object",
                "description": (
                    "Arguments to pass to the MCP tool. Shape depends on the "
                    "tool — call ``mcp_list`` first to discover its schema."
                ),
            },
        },
        "required": ["server", "tool"],
    },
    "mcp_connect": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Local name to register this server under.",
            },
            "transport": {
                "type": "string",
                "enum": ["stdio", "http", "streamable-http"],
                "description": "MCP transport type.",
            },
            "command": {
                "type": "string",
                "description": "Executable to launch (stdio transport).",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command-line arguments (stdio transport).",
            },
            "url": {
                "type": "string",
                "description": "Server URL (http / streamable-http transport).",
            },
            "env": {
                "type": "object",
                "description": "Extra environment variables for stdio transport.",
            },
            "connect_timeout": {
                "type": "number",
                "description": "Seconds to wait for handshake (default 10).",
            },
        },
        "required": ["name", "transport"],
    },
    "mcp_disconnect": {
        "type": "object",
        "properties": {
            "server": {
                "type": "string",
                "description": "Name of the MCP server to disconnect.",
            },
        },
        "required": ["server"],
    },
}
