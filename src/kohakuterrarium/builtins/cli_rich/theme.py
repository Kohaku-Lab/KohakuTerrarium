"""Theme constants for the rich CLI — colors, glyphs, status icons."""

ICON_RUNNING = "●"
ICON_DONE = "✓"
ICON_ERROR = "✗"
ICON_USER = "›"
ICON_AI = "◆"
ICON_THINKING = "…"
ICON_SUBAGENT = "↳"
ICON_BG = "⏳"
ICON_COMPACT = "⟳"

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

THINKING_LABEL = "KohakUwUing"


def spinner_frame(now: float, fps: float = 5.0) -> str:
    """Return the spinner frame for the given monotonic time."""
    idx = int(now * fps) % len(SPINNER_FRAMES)
    return SPINNER_FRAMES[idx]


def fmt_elapsed_compact(seconds: float) -> str:
    """Format elapsed time with stable precision across seconds, minutes, and hours."""
    if seconds < 0:
        seconds = 0.0
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs:02d}s"
    hours = int(seconds // 3600)
    rest = int(seconds % 3600)
    minutes = rest // 60
    secs = rest % 60
    return f"{hours}h {minutes:02d}m {secs:02d}s"


# Continuation lines align with the content column after the first gutter glyph.
GUTTER_GLYPH = "⎿ "
GUTTER_INDENT = "  "


COLOR_RUNNING = "yellow"
COLOR_DONE = "green"
COLOR_ERROR = "red"
COLOR_USER = "cyan"
COLOR_AI = "magenta"
COLOR_DIM = "bright_black"
COLOR_FOOTER = "dim"
COLOR_TOOL_BORDER = "dim cyan"
COLOR_SUBAGENT_BORDER = "dim magenta"
COLOR_BG = "bright_blue"
COLOR_BANNER = "bold magenta"
COLOR_COMPACT_BANNER = "bold yellow on grey15"
