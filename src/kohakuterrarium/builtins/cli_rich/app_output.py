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

    def on_token_update(self, prompt: int, completion: int, max_ctx: int = 0) -> None:
        self.live_region.update_footer_tokens(prompt, completion, max_ctx)
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
