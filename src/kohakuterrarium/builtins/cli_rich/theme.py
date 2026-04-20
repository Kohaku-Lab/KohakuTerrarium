"""Theme constants for the rich CLI — colors, glyphs, status icons."""

# Status glyphs
ICON_RUNNING = "●"
ICON_DONE = "✓"
ICON_ERROR = "✗"
ICON_USER = "›"
ICON_AI = "◆"
ICON_THINKING = "…"
ICON_SUBAGENT = "↳"
ICON_BG = "⏳"
ICON_COMPACT = "⟳"

# Spinner frames (used by live blocks)
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Thinking indicator label (Kohaku + UwU + ing)
THINKING_LABEL = "KohakUwUing"


def spinner_frame(now: float, fps: float = 5.0) -> str:
    """Return the spinner frame for the given monotonic time."""
    idx = int(now * fps) % len(SPINNER_FRAMES)
    return SPINNER_FRAMES[idx]


def fmt_elapsed_compact(seconds: float) -> str:
    """Format elapsed seconds as ``1.2s`` / ``12s`` / ``3m 05s`` / ``1h 04m 03s``.

    Sub-10s keeps 1 decimal so fast tools don't snap to ``0s``. Integer
    seconds past 10s avoid the flicker of sub-second digits updating.
    Past 60s switches to ``Xm YYs``; past 3600s to ``Xh YYm ZZs``.
    """
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


# Gutter prefix for the single ⎿ that marks the start of a tool body
# in the committed Panel. Claude-code style: one glyph at the top of
# the output block, continuation lines indent to match the content
# column.
GUTTER_GLYPH = "⎿ "
GUTTER_INDENT = "  "


# Colors (Rich-compatible)
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
