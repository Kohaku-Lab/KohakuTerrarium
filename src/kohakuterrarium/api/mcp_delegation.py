"""MCP schemas and calls for the registered local delegation surface."""

from mcp.types import Tool, ToolAnnotations

_TEXT = {"type": "string", "minLength": 1}
_SCHEMAS = {
    "delegation_targets": (
        "List locally registered Creature and one-shot subagent targets. No model is started.",
        {},
        [],
    ),
    "delegate": (
        "Submit work and return a job_id immediately. Omit session_id for a fresh context; "
        "supply it to continue a Creature session. Busy sessions reject new work. "
        "Creature completion means this turn ended, not that background work finished. "
        "Use job_wait/job_status for results. Do not resubmit after a lost reply: list jobs/sessions first.",
        {"target": _TEXT, "prompt": _TEXT, "session_id": _TEXT},
        ["target", "prompt"],
    ),
    "delegation_send": (
        "Supplement an active delegation. Delivery uses KT input semantics and may wait for a turn boundary. "
        "Does not create a new task or restart a completed subagent.",
        {"job_id": _TEXT, "content": _TEXT},
        ["job_id", "content"],
    ),
    "delegation_sessions": (
        "List this server's delegated sessions, including autonomous busy activity without a delegation job ID.",
        {},
        [],
    ),
    "delegation_history": (
        "Read paginated session activity, including autonomous turns, or the current public conversation snapshot. "
        "Activity cursors are stable; conversation offsets refer to a mutable snapshot (compaction may change it).",
        {
            "session_id": _TEXT,
            "cursor": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
            "view": {
                "type": "string",
                "enum": ["events", "conversation"],
                "default": "events",
            },
        },
        ["session_id"],
    ),
    "delegation_close": (
        "Close a server-owned session using KT stop, including triggers and all its managed background work. "
        "Closed sessions keep readable history but cannot be continued. To stop and later continue, cancel its active job instead.",
        {"session_id": _TEXT},
        ["session_id"],
    ),
}


def delegation_tools():
    """Return schemas only when delegation has registered targets."""
    return [
        Tool(
            name=name,
            description=description,
            inputSchema={
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            annotations=ToolAnnotations(
                readOnlyHint=name
                in {"delegation_targets", "delegation_sessions", "delegation_history"},
                openWorldHint=name
                not in {
                    "delegation_targets",
                    "delegation_sessions",
                    "delegation_history",
                },
            ),
        )
        for name, (description, properties, required) in _SCHEMAS.items()
    ]


async def call_delegation(runtime, name, args):
    """Route a validated MCP operation to its runtime owner."""
    match name:
        case "delegation_targets":
            return {"targets": runtime.targets()}
        case "delegate":
            return await runtime.submit(**args)
        case "delegation_send":
            return {"job_id": args["job_id"], "accepted": await runtime.send(**args)}
        case "delegation_sessions":
            return {"sessions": runtime.sessions()}
        case "delegation_history":
            return runtime.history(**args)
        case "delegation_close":
            await runtime.close_session(args["session_id"])
            return {"session_id": args["session_id"], "closed": True}
        case _:
            raise ValueError("Unknown delegation tool")
