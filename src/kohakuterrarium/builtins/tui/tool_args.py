"""Format tool arguments for compact and expanded TUI display."""

_TOOL_DISPLAY_ARG_KEYS: dict[str, tuple[str, ...]] = {
    "bash": ("command",),
    "read": ("path",),
    "write": ("file_path", "path"),
    "edit": ("file_path", "path"),
    "glob": ("pattern",),
    "grep": ("pattern", "path"),
    "send_message": ("channel",),
    "terrarium_send": ("channel",),
    "send_channel": ("channel",),
    "group_send": ("to",),
    "terrarium_observe": ("channel",),
    "info": ("name", "topic"),
}


def _display_args(tool_name: str, args: dict) -> list[tuple[str, object]]:
    """Return arguments permitted by the existing TUI preview boundary."""
    if not isinstance(args, dict):
        return []
    allowed_keys = _TOOL_DISPLAY_ARG_KEYS.get(tool_name)
    return [
        (key, value)
        for key, value in args.items()
        if key != "content"
        and not key.startswith("_")
        and (allowed_keys is None or key in allowed_keys)
    ]


def format_args_detail(tool_name: str, args: dict) -> str:
    """Format complete display-safe arguments for an expanded tool block."""
    return "\n".join(f"{key}={value}" for key, value in _display_args(tool_name, args))


def format_args_preview(tool_name: str, args: dict) -> str:
    """Format the leading argument summary used in a tool title."""
    if not args:
        return ""
    match tool_name:
        case "bash":
            return str(args.get("command", ""))
        case "read":
            return str(args.get("path", ""))
        case "write" | "edit":
            return str(args.get("file_path", args.get("path", "")))
        case "glob":
            return str(args.get("pattern", ""))
        case "grep":
            pattern = args.get("pattern", "")
            path = args.get("path", "")
            return f'"{pattern}" {path}'.strip()
        case "send_message" | "terrarium_send" | "send_channel":
            return f"-> {args.get('channel', '')}"
        case "group_send":
            return f"-> {args.get('to', '')}"
        case "terrarium_observe":
            return f"<- {args.get('channel', '')}"
        case "info":
            return str(args.get("name", args.get("topic", "")))
        case _:
            display_args = _display_args(tool_name, args)
            if display_args:
                key, value = display_args[0]
                return f"{key}={value}"
    return ""
