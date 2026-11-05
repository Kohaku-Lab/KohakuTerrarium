"""Short display strings for ktui: tool arguments, results, durations and token counts."""

import json
from typing import Any

# The argument that best identifies a call, per tool; other tools show their first string.
_KEY_ARGS: dict[str, tuple[str, ...]] = {
    "bash": ("command",),
    "shell": ("command",),
    "read": ("path", "file_path"),
    "write": ("path", "file_path"),
    "edit": ("path", "file_path"),
    "multi_edit": ("path", "file_path"),
    "glob": ("pattern",),
    "grep": ("pattern",),
    "web_fetch": ("url",),
    "web_search": ("query",),
}


# the noun of a tool-call group's header: "5 tool calls"
BATCH_TITLE = "tool calls"


def one_line(text: Any, limit: int = 80) -> str:
    """First line of ``text``, cut to ``limit`` characters."""
    line = str(text or "").strip().split("\n", 1)[0]
    return line if len(line) <= limit else line[: limit - 1] + "…"


def args_preview(tool: str, args: Any, limit: int = 72) -> str:
    """The identifying argument of a tool call, or a compact ``k=v`` list."""
    if not isinstance(args, dict) or not args:
        return one_line(args, limit) if isinstance(args, str) else ""
    for key in _KEY_ARGS.get(tool, ()):
        if args.get(key):
            return one_line(args[key], limit)
    for key, value in args.items():
        if isinstance(value, str) and value and not key.startswith("_"):
            return one_line(value, limit)
    shown = {k: v for k, v in args.items() if not k.startswith("_")}
    return one_line(json.dumps(shown, ensure_ascii=False, default=str), limit)


def result_summary(output: Any) -> str:
    """What a folded tool cell says about its result: a line count or the one line."""
    text = str(output or "").rstrip()
    if not text:
        return "no output"
    lines = text.count("\n") + 1
    return f"{lines} lines" if lines > 1 else one_line(text, 60)


def duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s"


def tokens(n: int) -> str:
    n = int(n or 0)
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def parse_detail(detail: str) -> tuple[str, str]:
    """Split KT's ``"[name] rest"`` activity detail; a ``[job]`` suffix on the name is dropped."""
    if detail.startswith("["):
        end = detail.find("]", 1)
        if end > 0:
            name = detail[1:end]
            if "[" in name:
                name = name[: name.index("[")]
            return name, detail[end + 2 :]
    return "", detail
