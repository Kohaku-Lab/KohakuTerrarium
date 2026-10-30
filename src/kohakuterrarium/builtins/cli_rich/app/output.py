"""Output-event mixin for RichCLIApp.

The agent's OutputRouter calls a set of ``on_*`` callbacks on the app
(``on_text_chunk``, ``on_tool_start``, ``on_tool_done``, etc.). There
are a lot of them — putting them on the main Application class pushes
that file past the 600-line guard. They all share the same shape:

  1. mutate ``self.live_region``
  2. optionally commit a renderable to scrollback via ``self.committer``
  3. invalidate the app for a redraw

Split as a mixin so ``app.py`` stays focused on lifecycle + layout.
"""

from typing import Any

from rich.markup import escape
from rich.panel import Panel

from kohakuterrarium.builtins.terminal_attention import set_attention


class AppOutputMixin:
    """Update live and scrollback output from agent events."""

    def on_text_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self.live_region.append_chunk(chunk)
        self._invalidate()

    def on_processing_start(self) -> None:
        # Multi-creature renderers share one terminal; only explicit input requests
        # are safe to aggregate there without target-keyed title state.
        if not getattr(self, "multi_creature_enabled", False):
            set_attention("working")
        # A turn is bounded by one blank line regardless of internal tool commits.
        self._commit_blank_line()
        self.live_region.start_message()
        self._invalidate()

    def on_processing_end(self) -> None:
        if not getattr(self, "multi_creature_enabled", False):
            set_attention("ready")
        committed = self.live_region.finish_message()
        if committed is not None:
            self._commit_renderable(committed)
        self._commit_blank_line()
        self._invalidate()

    def on_tool_start(
        self,
        job_id: str,
        name: str,
        args_preview: str = "",
        kind: str = "tool",
        parent_job_id: str = "",
        background: bool = False,
    ) -> None:
        # Direct tools split pre/post text around their scrollback block; background
        # tools keep the current assistant message intact across the dispatch.
        if not background:
            self._flush_assistant_message()
        self.live_region.add_tool(
            job_id, name, args_preview, kind, parent_job_id=parent_job_id
        )
        if background:
            self.live_region.promote_tool(job_id)
            block = self.live_region.tool_blocks.get(job_id)
            if block is not None and not parent_job_id:
                self.committer.renderable(block.build_dispatch_notice())
        self._invalidate()

    def _flush_assistant_message(self) -> None:
        msg = self.live_region.assistant_msg
        if msg is None or msg.is_empty:
            return
        committed = self.live_region.finish_message()
        if committed is not None:
            self._commit_renderable(committed)

    def on_tool_done(self, job_id: str, output: str = "", **metadata) -> None:
        committed = self.live_region.update_tool_done(job_id, output, **metadata)
        if committed is not None:
            # Consecutive blocks share a separator through the committer.
            self.committer.block_renderable(committed)
        self._invalidate()

    def on_tool_error(self, job_id: str, error: str = "") -> None:
        committed = self.live_region.update_tool_error(job_id, error)
        if committed is not None:
            self.committer.block_renderable(committed)
        self._invalidate()

    def on_tool_promoted(self, job_id: str) -> None:
        self.live_region.promote_tool(job_id)
        self._invalidate()

    def on_job_cancelled(self, job_id: str, job_name: str = "") -> None:
        committed = self.live_region.cancel_tool(job_id)
        if committed is not None:
            self.committer.block_renderable(committed)
        self._invalidate()

    def on_subagent_tool_start(
        self, parent_id: str, tool_name: str, args_preview: str = ""
    ) -> None:
        self.live_region.add_subagent_tool(parent_id, tool_name, args_preview)
        self._invalidate()

    def on_subagent_tool_done(
        self, parent_id: str, tool_name: str, output: str = ""
    ) -> None:
        self.live_region.update_subagent_tool_done(parent_id, tool_name, output)
        self._invalidate()

    def on_subagent_tool_error(
        self, parent_id: str, tool_name: str, error: str = ""
    ) -> None:
        self.live_region.update_subagent_tool_error(parent_id, tool_name, error)
        self._invalidate()

    def on_subagent_tokens(
        self, parent_id: str, prompt: int, completion: int, total: int
    ) -> None:
        self.live_region.update_subagent_tokens(parent_id, prompt, completion, total)
        self._invalidate()

    def on_token_update(
        self,
        prompt: int,
        completion: int,
        max_ctx: int = 0,
        cached: int = 0,
    ) -> None:
        self.live_region.update_footer_tokens(prompt, completion, max_ctx, cached)
        self._invalidate()

    def on_compact_start(self) -> None:
        self.live_region.set_compacting(True)
        self._invalidate()

    def on_compact_end(self) -> None:
        self.live_region.set_compacting(False)
        self._invalidate()

    def on_session_info(self, model: str = "", max_ctx: int = 0) -> None:
        if model:
            self.live_region.update_footer_model(model)
        if max_ctx:
            self.live_region.footer._max_context = max_ctx
        self._invalidate()

    def on_processing_error(self, error_type: str, error: str) -> None:
        """Surface a processing error as a red notice in scrollback."""
        self._flush_assistant_message()
        self.committer.text(f"[red]✗ {escape(error_type)}:[/red] {escape(error)}")
        self._invalidate()

    def on_interrupt_notice(self, detail: str = "") -> None:
        """Commit an 'interrupted' notice to scrollback."""
        self._flush_assistant_message()
        self.committer.text("[yellow]⚠ interrupted[/yellow]")
        self._invalidate()

    def on_drive_turn(self, metadata: dict[str, Any]) -> None:
        """Commit a marker for a turn that a Drive delivery started."""
        self._flush_assistant_message()
        kind = escape(str(metadata.get("drive_kind") or "drive"))
        drive_id = escape(str(metadata.get("drive_id") or ""))
        reason = escape(str(metadata.get("delivery_reason") or ""))
        line = f"[magenta]◆ drive turn · {kind} {drive_id} ({reason})[/magenta]"
        objective = str(metadata.get("objective") or "")
        if objective:
            line += f"[dim] — {escape(objective)}[/dim]"
        self.committer.text(line)
        self._invalidate()

    def on_background_result(self, kind: str, label: str, count: int = 1) -> None:
        """Commit a background-result delivery notice to scrollback."""
        self._flush_assistant_message()
        noun = "result" if count == 1 else "results"
        prefix = f"{escape(kind)} " if kind and kind != "mixed" else ""
        self.committer.text(
            f"[cyan]⟲ background {prefix}{noun} delivered: {escape(label)}[/cyan]"
        )
        self._invalidate()

    def on_ui_event_panel(self, event_type: str, payload: dict) -> None:
        """Render an interactive event as an informational fallback panel."""
        self._flush_assistant_message()
        prompt = payload.get("prompt", "")
        title_map = {
            "confirm": "Confirm",
            "ask_text": "Input requested",
            "selection": "Selection requested",
        }
        title = title_map.get(event_type, event_type)
        body_lines = [escape(str(prompt))] if prompt else []
        if event_type == "confirm":
            options = payload.get("options") or []
            for opt in options:
                body_lines.append(
                    f"  • {escape(str(opt.get('label', opt.get('id', '?'))))}"
                    f" ({escape(str(opt.get('style', 'secondary')))})"
                )
            detail = payload.get("detail")
            if detail:
                body_lines.insert(0, escape(str(detail)) + "\n")
        elif event_type == "ask_text":
            placeholder = payload.get("placeholder")
            if placeholder:
                body_lines.append(f"  hint: {escape(str(placeholder))}")
        elif event_type == "selection":
            options = payload.get("options") or []
            for i, opt in enumerate(options, 1):
                desc = opt.get("description")
                selection_index = escape(f"[{i}]")
                line = (
                    f"  {selection_index} "
                    f"{escape(str(opt.get('label', opt.get('id', '?'))))}"
                )
                if desc:
                    line += f"  — {escape(str(desc))}"
                body_lines.append(line)
        body_lines.append("")
        body_lines.append("[dim](respond via TUI or web frontend)[/dim]")
        self.committer.commit(
            Panel(
                "\n".join(body_lines),
                title=escape(str(title)),
                border_style="cyan",
            )
        )
        self._invalidate()

    def on_progress_event(
        self,
        event_id: str | None,
        update_target: str | None,
        payload: dict,
    ) -> None:
        """Render progress events as append-only scrollback notices."""
        label = payload.get("label", "progress")
        value = payload.get("value", 0)
        max_v = payload.get("max", 0)
        complete = bool(payload.get("complete"))
        if complete:
            self.committer.text(f"[green]✓ {escape(str(label))}[/green]")
        elif update_target:
            pct = ""
            if (
                isinstance(value, (int, float))
                and isinstance(max_v, (int, float))
                and max_v
            ):
                pct = f" ({int(value * 100 / max_v)}%)"
            self.committer.text(f"  … {escape(str(label))}{pct}")
        else:
            self.committer.text(f"[cyan]▸ {escape(str(label))}[/cyan]")
        self._invalidate()

    def on_notification_event(self, payload: dict) -> None:
        level = payload.get("level", "info")
        text = payload.get("text", "")
        title = payload.get("title")
        color = {
            "info": "cyan",
            "success": "green",
            "warning": "yellow",
            "error": "red",
        }.get(level, "cyan")
        prefix = (
            f"[{color}]{escape(str(title))}:[/{color}] "
            if title
            else f"[{color}]·[/{color}] "
        )
        self.committer.text(prefix + escape(str(text)))
        self._invalidate()

    def on_card_event(self, payload: dict) -> None:
        """Render a card as a styled Rich Panel."""
        self._flush_assistant_message()
        title = payload.get("title", "")
        subtitle = payload.get("subtitle", "")
        icon = payload.get("icon", "")
        accent = payload.get("accent", "neutral")
        accent_map = {
            "primary": "cyan",
            "info": "blue",
            "success": "green",
            "warning": "yellow",
            "danger": "red",
            "neutral": "white",
        }
        border = accent_map.get(accent, "white")
        header = f"{icon} {title}".strip() if icon else title
        header = escape(str(header))
        if subtitle:
            header = f"{header}  [dim]{escape(str(subtitle))}[/dim]"
        body_parts: list[str] = []
        body = payload.get("body")
        if body:
            body_parts.append(escape(str(body)))
        fields = payload.get("fields") or []
        if fields:
            field_lines = [
                f"  [bold]{escape(str(f.get('label', '')))}:[/bold] "
                f"{escape(str(f.get('value', '')))}"
                for f in fields
            ]
            body_parts.append("\n".join(field_lines))
        actions = payload.get("actions") or []
        if actions:
            action_colors = {
                "primary": "cyan",
                "secondary": "white",
                "danger": "red",
                "link": "blue",
            }
            action_parts = []
            for action in actions:
                action_style = action_colors.get(action.get("style"), "white")
                action_label = escape(str(action.get("label", action.get("id", "?"))))
                action_parts.append(f"[{action_style}]\\[{action_label}][/]")
            act_line = "  ".join(action_parts)
            body_parts.append(act_line)
            body_parts.append("[dim](respond via TUI or web frontend)[/dim]")
        footer = payload.get("footer", "")
        if footer:
            body_parts.append(f"[dim]{escape(str(footer))}[/dim]")
        self.committer.commit(
            Panel("\n\n".join(body_parts), title=header, border_style=border)
        )
        self._invalidate()
