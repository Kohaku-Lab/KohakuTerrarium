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

from rich.panel import Panel


class AppOutputMixin:
    """Receives output events from the agent and updates the live region."""

    # The concrete class provides these — declared only for type hints.
    # Runtime references resolve against the combined instance.

    # ── Streaming text ──

    def on_text_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self.live_region.append_chunk(chunk)
        self._invalidate()

    def on_processing_start(self) -> None:
        # Spacer line before the model's response. Tool calls / text
        # commits inside the turn add no extra blank lines, so the whole
        # turn reads as one block surrounded by exactly one blank line
        # before and one after.
        self._commit_blank_line()
        self.live_region.start_message()
        self._invalidate()

    def on_processing_end(self) -> None:
        committed = self.live_region.finish_message()
        if committed is not None:
            self._commit_renderable(committed)
        self._commit_blank_line()
        self._invalidate()

    # ── Tool lifecycle ──

    def on_tool_start(
        self,
        job_id: str,
        name: str,
        args_preview: str = "",
        kind: str = "tool",
        parent_job_id: str = "",
        background: bool = False,
    ) -> None:
        # Ordering rule:
        #
        # - Direct (blocking) tools — the controller WAITS for the tool
        #   inside the same turn, then the model continues with post-tool
        #   text. We flush the in-flight assistant message NOW so the
        #   commit order in scrollback is: pre-text → tool → post-text.
        #
        # - Background tools — the controller does NOT wait. It feeds a
        #   "task promoted" placeholder back to the LLM, which generates
        #   interim text in the same cycle. If we flushed here, the
        #   pre-tool text and the interim text would end up as TWO
        #   separate ◆ blocks. Keeping the assistant message intact
        #   across a bg dispatch lets the whole cycle commit as one ◆.
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
            # Tool/sub-agent commits go through block_renderable so the
            # committer can share rule separators between consecutive
            # blocks (one line between two tools instead of two).
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

    # ── Sub-agent nested tool events ──

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

    # ── Footer / session info ──

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

    # ── Errors / interrupts ──

    def on_processing_error(self, error_type: str, error: str) -> None:
        """Surface a processing error as a red notice in scrollback."""
        self._flush_assistant_message()
        self.committer.text(f"[red]✗ {error_type}:[/red] {error}")
        self._invalidate()

    def on_interrupt_notice(self, detail: str = "") -> None:
        """Commit an 'interrupted' notice to scrollback."""
        self._flush_assistant_message()
        self.committer.text("[yellow]⚠ interrupted[/yellow]")
        self._invalidate()

    # ── Phase B UI events (display-only in CLI v1) ──

    def on_ui_event_panel(self, event_type: str, payload: dict) -> None:
        """Render an interactive event (confirm/ask_text/selection) as
        an informational Rich panel. CLI v1 does not capture replies —
        TUI/web are the interactive renderers; the panel just shows
        what the agent is waiting on so a CLI user knows the state.
        """
        self._flush_assistant_message()
        prompt = payload.get("prompt", "")
        title_map = {
            "confirm": "Confirm",
            "ask_text": "Input requested",
            "selection": "Selection requested",
        }
        title = title_map.get(event_type, event_type)
        body_lines = [prompt] if prompt else []
        if event_type == "confirm":
            options = payload.get("options") or []
            for opt in options:
                body_lines.append(
                    f"  • {opt.get('label', opt.get('id', '?'))}"
                    f" ({opt.get('style', 'secondary')})"
                )
            detail = payload.get("detail")
            if detail:
                body_lines.insert(0, detail + "\n")
        elif event_type == "ask_text":
            placeholder = payload.get("placeholder")
            if placeholder:
                body_lines.append(f"  hint: {placeholder}")
        elif event_type == "selection":
            options = payload.get("options") or []
            for i, opt in enumerate(options, 1):
                desc = opt.get("description")
                line = f"  [{i}] {opt.get('label', opt.get('id', '?'))}"
                if desc:
                    line += f"  — {desc}"
                body_lines.append(line)
        body_lines.append("")
        body_lines.append("[dim](respond via TUI or web frontend)[/dim]")
        self.committer.commit(
            Panel("\n".join(body_lines), title=title, border_style="cyan")
        )
        self._invalidate()

    def on_progress_event(
        self,
        event_id: str | None,
        update_target: str | None,
        payload: dict,
    ) -> None:
        """Display-only progress rendering for CLI v1.

        Updates of an existing progress event print a one-liner;
        complete events print a final tick. CLI does not maintain
        live progress widgets.
        """
        label = payload.get("label", "progress")
        value = payload.get("value", 0)
        max_v = payload.get("max", 0)
        complete = bool(payload.get("complete"))
        if complete:
            self.committer.text(f"[green]✓ {label}[/green]")
        elif update_target:
            pct = ""
            if (
                isinstance(value, (int, float))
                and isinstance(max_v, (int, float))
                and max_v
            ):
                pct = f" ({int(value * 100 / max_v)}%)"
            self.committer.text(f"  … {label}{pct}")
        else:
            self.committer.text(f"[cyan]▸ {label}[/cyan]")
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
        prefix = f"[{color}]{title}:[/{color}] " if title else f"[{color}]·[/{color}] "
        self.committer.text(prefix + text)
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
        if subtitle:
            header = f"{header}  [dim]{subtitle}[/dim]"
        body_parts: list[str] = []
        body = payload.get("body")
        if body:
            body_parts.append(body)
        fields = payload.get("fields") or []
        if fields:
            field_lines = [
                f"  [bold]{f.get('label', '')}:[/bold] {f.get('value', '')}"
                for f in fields
            ]
            body_parts.append("\n".join(field_lines))
        actions = payload.get("actions") or []
        if actions:
            act_line = "  ".join(
                f"[{a.get('style', 'secondary')}]\\[{a.get('label', a.get('id', '?'))}][/]"
                for a in actions
            )
            body_parts.append(act_line)
            body_parts.append("[dim](respond via TUI or web frontend)[/dim]")
        footer = payload.get("footer", "")
        if footer:
            body_parts.append(f"[dim]{footer}[/dim]")
        self.committer.commit(
            Panel("\n\n".join(body_parts), title=header, border_style=border)
        )
        self._invalidate()
