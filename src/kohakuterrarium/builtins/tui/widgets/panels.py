import time

from textual.message import Message
from textual.widgets import Static

from kohakuterrarium.builtins.tui.widgets.helpers import _fmt_tokens

class RunningPanel(Static):
    """Display running jobs and emit cancellation or promotion requests."""

    class CancelRequested(Message):
        def __init__(self, job_id: str, job_name: str) -> None:
            super().__init__()
            self.job_id = job_id
            self.job_name = job_name

    class PromoteRequested(Message):
        """Request background promotion for a running task."""

        def __init__(self, job_id: str) -> None:
            super().__init__()
            self.job_id = job_id

    DEFAULT_CSS = """
    RunningPanel {
        height: auto;
        max-height: 12;
        padding: 0 1;
        border: round #D4920A;
        border-title-color: #D4920A;
        border-title-align: left;
    }
    RunningPanel:hover {
        border: round #E74C3C;
    }
    """

    # Promotion appears only after a task has run long enough to benefit from it.
    PROMOTE_THRESHOLD = 1.0

    def __init__(self, **kwargs):
        super().__init__("(idle)", **kwargs)
        self.border_title = "Running"
        # Values carry label, start time, and whether the job supports promotion.
        self._items: dict[str, tuple[str, float, bool]] = {}
        self._ordered_ids: list[str] = []

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        if self._items:
            self._refresh_display()

    def add_item(self, item_id: str, label: str, promotable: bool = False) -> None:
        self._items[item_id] = (label, time.monotonic(), promotable)
        self._rebuild_order()
        self._refresh_display()

    def remove_item(self, item_id: str) -> None:
        self._items.pop(item_id, None)
        self._rebuild_order()
        self._refresh_display()

    def clear(self) -> None:
        self._items.clear()
        self._ordered_ids.clear()
        self._refresh_display()

    def _rebuild_order(self) -> None:
        self._ordered_ids = list(self._items.keys())

    def on_click(self, event) -> None:
        """Promote eligible right-side clicks; cancel all other clicks."""
        if not self._items:
            return

        idx = 0
        if len(self._items) == 1:
            idx = 0
        else:
            idx = min(event.y, len(self._ordered_ids) - 1)
            idx = max(idx, 0)

        job_id = self._ordered_ids[idx]
        label, start, promotable = self._items[job_id]
        elapsed = time.monotonic() - start

        if promotable and elapsed >= self.PROMOTE_THRESHOLD and event.x > 35:
            self.post_message(self.PromoteRequested(job_id))
        else:
            self.post_message(self.CancelRequested(job_id, label))

    def _refresh_display(self) -> None:
        if not self._items:
            self.update("(idle)")
            self.border_title = "Running"
            return
        lines = []
        for item_id in self._ordered_ids:
            label, start, promotable = self._items[item_id]
            elapsed = time.monotonic() - start
            if promotable and elapsed >= self.PROMOTE_THRESHOLD:
                lines.append(f"○ {label}  ({elapsed:.0f}s)  [→bg]")
            else:
                lines.append(f"○ {label}  ({elapsed:.0f}s)")
        self.border_title = f"Running ({len(self._items)})"
        self.update("\n".join(lines))


class ScratchpadPanel(Static):
    DEFAULT_CSS = """
    ScratchpadPanel {
        height: auto;
        max-height: 10;
        padding: 0 1;
        border: round #0F52BA;
        border-title-color: #0F52BA;
        border-title-align: left;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("(empty)", **kwargs)
        self.border_title = "Scratchpad"

    def update_data(self, data: dict) -> None:
        if not data:
            self.update("(empty)")
            return
        lines = [f"{k}: {str(v)[:60]}" for k, v in data.items()]
        self.update("\n".join(lines))


class SessionInfoPanel(Static):
    DEFAULT_CSS = """
    SessionInfoPanel {
        height: auto;
        max-height: 8;
        padding: 0 1;
        border: round #4C9989;
        border-title-color: #4C9989;
        border-title-align: left;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self.border_title = "Session"
        self._start_time = time.monotonic()
        self._input_tokens = 0
        self._output_tokens = 0
        self._cached_tokens = 0
        self._last_prompt_tokens = 0
        self._max_context = 0
        self._compact_threshold = 0
        self._model = ""
        self._session_id = ""
        self._agent_name = ""

    def set_info(
        self, session_id: str = "", model: str = "", agent_name: str = ""
    ) -> None:
        self._session_id = session_id
        self._model = model
        self._agent_name = agent_name
        self._refresh()

    def set_model(self, model: str) -> None:
        """Update the model without replacing session identity details."""
        self._model = model
        self._refresh()

    def add_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        self._input_tokens += prompt_tokens
        self._output_tokens += completion_tokens
        self._cached_tokens += cached_tokens
        self._last_prompt_tokens = prompt_tokens
        self._refresh()

    def restore_usage(
        self, total_in: int, total_out: int, last_prompt: int, total_cached: int = 0
    ) -> None:
        """Restore cumulative token totals from session history."""
        self._input_tokens = total_in
        self._output_tokens = total_out
        self._cached_tokens = total_cached
        self._last_prompt_tokens = last_prompt
        self._refresh()

    def add_tokens(self, count: int) -> None:
        """Add legacy token counts to the input total."""
        self._input_tokens += count
        self._refresh()

    def set_context_limits(self, max_context: int, compact_threshold: int = 0) -> None:
        self._max_context = max_context
        self._compact_threshold = compact_threshold
        self._refresh()

    def set_compact_threshold(self, threshold_tokens: int) -> None:
        """Set the compaction threshold for compatibility callers."""
        self._compact_threshold = threshold_tokens
        self._refresh()

    def _refresh(self) -> None:
        elapsed = time.monotonic() - self._start_time
        mins, secs = int(elapsed // 60), int(elapsed % 60)
        lines = []
        if self._agent_name:
            lines.append(f"Agent: {self._agent_name}")
        if self._session_id:
            lines.append(f"ID: {self._session_id[:20]}")
        if self._model:
            lines.append(f"Model: {self._model}")
        lines.append(f"Runtime: {mins}m {secs}s")
        total = self._input_tokens + self._output_tokens
        if total > 0:
            in_part = f"In: {_fmt_tokens(self._input_tokens)}"
            if self._cached_tokens > 0:
                in_part += f" (cache {_fmt_tokens(self._cached_tokens)})"
            lines.append(f"{in_part}  Out: {_fmt_tokens(self._output_tokens)}")
        max_ctx = self._max_context or self._compact_threshold
        if max_ctx > 0:
            ctx_used = (
                _fmt_tokens(self._last_prompt_tokens)
                if self._last_prompt_tokens
                else "0"
            )
            pct = (
                int(self._last_prompt_tokens / max_ctx * 100)
                if self._last_prompt_tokens
                else 0
            )
            compact_pct = ""
            if self._compact_threshold and max_ctx:
                compact_pct = f"/{int(self._compact_threshold / max_ctx * 100)}%"
            lines.append(
                f"Context: {ctx_used}/{_fmt_tokens(max_ctx)}" f" ({pct}%{compact_pct})"
            )
        self.update("\n".join(lines))


class TerrariumPanel(Static):
    """Display creatures and channels in terrarium mode."""

    DEFAULT_CSS = """
    TerrariumPanel {
        height: auto;
        max-height: 16;
        padding: 0 1;
        border: round #A57EAE;
        border-title-color: #A57EAE;
        border-title-align: left;
    }
    """

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self.border_title = "Terrarium"
        self._creatures: list[dict] = []
        self._channels: list[dict] = []

    def set_topology(self, creatures: list[dict], channels: list[dict]) -> None:
        """Replace the displayed creature and channel topology."""
        self._creatures = creatures
        self._channels = channels
        self._refresh()

    def _refresh(self) -> None:
        lines = []
        if self._creatures:
            lines.append("Creatures:")
            for c in self._creatures:
                icon = "\u25cf" if c.get("running") else "\u25cb"
                listen = ", ".join(c.get("listen", []))
                send = ", ".join(c.get("send", []))
                lines.append(f"  {icon} {c['name']}")
                if listen:
                    lines.append(f"    listen: {listen}")
                if send:
                    lines.append(f"    send: {send}")
        if self._channels:
            if lines:
                lines.append("")
            lines.append("Channels:")
            for ch in self._channels:
                ctype = ch.get("type", "queue")
                lines.append(f"  {ch['name']}  ({ctype})")
        self.update("\n".join(lines) if lines else "(no topology)")


class LoadOlderButton(Static):
    """Request a batch of hidden earlier messages."""

    DEFAULT_CSS = """
    LoadOlderButton {
        height: 1;
        text-align: center;
        color: #0F52BA;
        padding: 0 1;
    }
    LoadOlderButton:hover {
        background: #0F52BA 15%;
        text-style: underline;
    }
    """

    def __init__(self, hidden_count: int, **kwargs):
        super().__init__(
            f"\u25b2 Load {min(hidden_count, 30)} older messages ({hidden_count} hidden)",
            **kwargs,
        )
        self.hidden_count = hidden_count

    class Clicked(Message):
        pass

    def on_click(self) -> None:
        self.post_message(self.Clicked())
