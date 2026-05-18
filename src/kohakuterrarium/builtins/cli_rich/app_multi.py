"""Multi-creature mixin for :class:`RichCLIApp`.

Topic 08 — keeps the per-creature wiring / focus controller / @name
routing / multiplex demux off the main ``app.py`` so that file stays
under the 1000-line hard cap. The mixin assumes ``self`` is a
``RichCLIApp`` instance (it touches ``self.composer``, ``self.agent``,
``self.live_region``, ``self._invalidate``, ``self.on_*`` callbacks).

The mixin is inert when ``multi_creature_enabled`` stays False, so
single-creature usage of ``RichCLIApp(agent)`` is unaffected by the
mere presence of the methods.

Runtime graph change handling
-----------------------------

When ``setup_multi_creature`` finishes wiring the initial roster, it
also spawns :meth:`_watch_engine` — an async task that subscribes to
``engine.subscribe()`` with a ``CREATURE_STARTED`` / ``CREATURE_STOPPED``
filter. Every event flows through :meth:`_on_creature_started` or
:meth:`_on_creature_stopped`, which:

- **start**: builds the per-creature ``LiveRegionState`` + a fresh
  ``LiveRegion`` widget, mounts a ``MultiplexedRichOutput`` sink on
  the new creature (saving the previous sink into ``_managed_outputs``
  so teardown can restore it), and tells the focus controller a new
  creature is selectable.
- **stop**: restores the saved sink, drops local state, and pops the
  creature out of the focus controller (which may pick a sibling as
  the new focus if the removed creature was active).

Both are idempotent — duplicate events are no-ops. The watcher only
acts on creature ids it actually knows about (it filters by engine
ownership so events for graphs we don't own are ignored).

The single ``engine.subscribe()`` call lives for the lifetime of the
app. ``teardown_multi_creature`` cancels the watcher and restores every
managed sink so the engine's own teardown reaches its original modules.
"""

import asyncio
from dataclasses import replace as _dc_replace
from typing import Any

from kohakuterrarium.builtins.cli_rich.creature_status import (
    CreatureStatus,
    derive_status,
)
from kohakuterrarium.builtins.cli_rich.dialogs.agent_overlay import AgentOverlay
from kohakuterrarium.builtins.cli_rich.dialogs.peek_panel import PeekPanel
from kohakuterrarium.builtins.cli_rich.focus import FocusController
from kohakuterrarium.builtins.cli_rich.live_region import LiveRegion
from kohakuterrarium.builtins.cli_rich.live_state import LiveRegionState
from kohakuterrarium.builtins.cli_rich.multiplex import MultiplexedRichOutput
from kohakuterrarium.builtins.cli_rich.roster import RosterWidget
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.utils.logging import get_logger

# Slash commands that need the engine + creature_id in their context;
# dispatched locally by the mixin instead of the agent-level handler.
_TOPOLOGY_COMMANDS = frozenset(
    {"stop", "start", "spawn", "jobs", "channels", "scratchpad", "pad"}
)

logger = get_logger(__name__)


class AppMultiCreatureMixin:
    """Per-creature surface for :class:`RichCLIApp` — opt-in via
    :meth:`setup_multi_creature` from ``run_engine_with_rich_cli``."""

    def setup_multi_creature(self, engine: Any, focus_creature_id: str) -> None:
        self.engine = engine
        self.multi_creature_enabled = True
        creatures = list(engine.list_creatures())
        ids = [c.creature_id for c in creatures]
        self.focus_controller = FocusController(
            creature_ids=ids, focus_id=focus_creature_id
        )
        # Per-creature LiveRegion widget so Tab can swap the visible
        # output buffer + tool blocks. The FIRST creature reuses the
        # already-constructed ``self.live_region`` (which is wired into
        # the prompt_toolkit Layout) — swapping its content means
        # re-pointing ``self.live_region`` at whichever widget belongs
        # to the focused creature on each focus change.
        self.live_region_widgets: dict[str, LiveRegion] = {}
        # Track previous default_output per creature so dynamic
        # remove + teardown can restore.  Populated by mount paths.
        self._managed_outputs: dict[str, Any] = {}
        # Engine-subscription task — installed by start_engine_watch();
        # cancelled by teardown_multi_creature().
        self._engine_watch_task: asyncio.Task | None = None
        for c in creatures:
            self._install_creature_slot(c, is_focus=c.creature_id == focus_creature_id)
        focus = self._get_focus_creature()
        if focus is not None:
            self.agent = focus.agent
            self.composer.creature_name = focus.name or focus.creature_id
        self.roster = RosterWidget(
            get_statuses=self._collect_statuses,
            get_focus_id=lambda: self.focus_controller.focus_id,
        )
        self.agent_overlay = AgentOverlay(
            get_statuses=self._collect_statuses,
            on_focus=lambda cid: self.set_focus(cid),
            on_peek=lambda _cid: self._invalidate(),
        )
        self.peek_panel = PeekPanel(
            get_state=lambda cid: self.live_regions.get(cid) if cid else None,
            get_name=lambda cid: self._creature_name(cid),
        )
        # B2 redraw — pin the committer's capture bucket to the initial
        # focused creature so its commits start landing in the right log
        # before the first Tab. ``_handle_creature_event`` briefly flips
        # this for non-focus dispatch so bob's commits go into bob's
        # bucket while alice stays focused.
        try:
            self.committer.set_capture_target(focus_creature_id)
        except Exception:
            pass

    def _install_creature_slot(self, creature: Any, *, is_focus: bool) -> None:
        """Set up per-creature state + widget. Used by setup + runtime add."""
        cid = creature.creature_id
        if cid in self.live_regions:
            return
        self.live_regions[cid] = LiveRegionState(creature_id=cid)
        self.draft_by_creature.setdefault(cid, "")
        if is_focus and cid not in self.live_region_widgets:
            self.live_region_widgets[cid] = self.live_region
        else:
            self.live_region_widgets[cid] = LiveRegion()
        # Seed the per-widget footer from the creature's agent so its
        # model + context-size show correctly the first time the user
        # switches to it (otherwise the footer is blank until the agent
        # emits an event that updates it).
        widget = self.live_region_widgets[cid]
        agent = getattr(creature, "agent", None)
        if agent is not None:
            try:
                model = (
                    agent.llm_identifier()
                    or getattr(getattr(agent, "llm", None), "model", "")
                    or ""
                )
            except Exception:
                model = ""
            if model:
                widget.update_footer_model(model)
            max_ctx = getattr(getattr(agent, "llm", None), "_profile_max_context", 0)
            if max_ctx:
                widget.footer._max_context = max_ctx

    def _get_focus_creature(self) -> Any:
        if self.engine is None or not self.focus_controller.focus_id:
            return None
        try:
            return self.engine.get_creature(self.focus_controller.focus_id)
        except Exception:
            return None

    def _creature_name(self, creature_id: str | None) -> str:
        if not creature_id or self.engine is None:
            return ""
        try:
            return self.engine.get_creature(creature_id).name or creature_id
        except Exception:
            return creature_id or ""

    def _collect_statuses(self) -> list[CreatureStatus]:
        if self.engine is None:
            return []
        out: list[CreatureStatus] = []
        for c in self.engine.list_creatures():
            status = derive_status(c)
            state = self.live_regions.get(c.creature_id)
            if state is not None and state.unread_since_focus > 0:
                status = _dc_replace(status, unread=state.unread_since_focus)
            out.append(status)
        return out

    def resolve_creature_by_name(self, name: str) -> Any:
        if self.engine is None or not name:
            return None
        target = name.strip()
        try:
            return self.engine.get_creature(target)
        except Exception:
            pass
        for c in self.engine.list_creatures():
            if c.name == target or c.creature_id == target:
                return c
        lo = target.lower()
        for c in self.engine.list_creatures():
            if (c.name or "").lower().startswith(lo):
                return c
        return None

    def focus_next(self) -> None:
        if not self.multi_creature_enabled or self.focus_controller.count <= 1:
            return
        old = self.focus_controller.focus_id
        self._save_draft(old)
        new = self.focus_controller.next()
        self._on_focus_changed(old, new)

    def focus_prev(self) -> None:
        if not self.multi_creature_enabled or self.focus_controller.count <= 1:
            return
        old = self.focus_controller.focus_id
        self._save_draft(old)
        new = self.focus_controller.prev()
        self._on_focus_changed(old, new)

    def set_focus(self, creature_id: str) -> None:
        if not self.multi_creature_enabled:
            return
        old = self.focus_controller.focus_id
        if not self.focus_controller.set(creature_id):
            return
        self._save_draft(old)
        self._on_focus_changed(old, creature_id)

    def _save_draft(self, creature_id: str) -> None:
        if not creature_id:
            return
        try:
            self.draft_by_creature[creature_id] = self.composer.text_area.text
        except Exception:
            pass

    def _on_focus_changed(self, old_focus: str, new_focus: str) -> None:
        if old_focus == new_focus:
            return
        focus = self._get_focus_creature()
        if focus is not None:
            self.agent = focus.agent
            self.composer.creature_name = focus.name or new_focus
        # Swap the visible LiveRegion widget — Tab now actually
        # changes what's drawn in the status area, not just the prompt
        # prefix. ``self.live_region`` is the reference every render
        # path reads from; pointing it at the focused creature's
        # widget is the entire context swap.
        widget = self.live_region_widgets.get(new_focus)
        if widget is not None:
            self.live_region = widget
        # Refresh the new widget's footer from the focused agent so
        # model / context-size / token totals match what the new
        # creature is actually running (otherwise the per-widget footer
        # would show whatever it was last set to — usually blank).
        self._refresh_footer_from_focus()
        state = self.live_regions.get(new_focus)
        if state is not None:
            state.reset_unread()
        try:
            self.composer.text_area.text = self.draft_by_creature.get(new_focus, "")
        except Exception:
            pass
        try:
            self.composer.set_command_context(agent=self.agent)
        except Exception:
            pass
        # B2 redraw — wipe scrollback and re-emit only the focused
        # creature's captured commits. Then re-point the capture target
        # so future commits land in the right bucket.
        self.redraw_focused()
        try:
            self.committer.set_capture_target(new_focus)
        except Exception:
            pass
        self._invalidate()

    def _refresh_footer_from_focus(self) -> None:
        """Copy the focused agent's model + context size into the live widget."""
        agent = self.agent
        if agent is None or self.live_region is None:
            return
        try:
            model = agent.llm_identifier() or getattr(agent.llm, "model", "") or ""
        except Exception:
            model = ""
        if model:
            self.live_region.update_footer_model(model)
        max_ctx = getattr(agent.llm, "_profile_max_context", 0) or 0
        if max_ctx:
            self.live_region.footer._max_context = max_ctx

    async def inject_to_creature(self, creature_id: str, text: str) -> None:
        if self.engine is None:
            return
        try:
            c = self.engine.get_creature(creature_id)
        except Exception:
            return
        try:
            await c.agent.inject_input(text, source="cli")
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("inject_to_creature failed", error=str(e))

    async def broadcast_to_all(self, text: str) -> None:
        if self.engine is None:
            return
        focus = self._get_focus_creature()
        if focus is None or not getattr(focus, "is_privileged", False):
            self.on_processing_error("@all", "requires a privileged focused creature")
            return
        for c in self.engine.list_creatures():
            try:
                await c.agent.inject_input(text, source="cli")
            except Exception as e:  # pragma: no cover - defensive
                logger.exception(
                    "broadcast inject failed",
                    creature_id=c.creature_id,
                    error=str(e),
                )

    async def _handle_creature_event(
        self, creature_id: str, kind: str, payload: dict
    ) -> None:
        if not self.multi_creature_enabled:
            return
        state = self.live_regions.get(creature_id)
        if state is None:
            return
        is_focus = creature_id == self.focus_controller.focus_id
        # Route into the creature's OWN LiveRegion widget by swapping
        # ``self.live_region`` for the duration of the call. The
        # existing ``on_text_chunk`` / ``on_processing_*`` /
        # ``on_activity_with_metadata`` handlers all read
        # ``self.live_region`` — swapping the reference lets every
        # event reach the right widget without per-handler refactors.
        # Non-focused widgets accumulate silently; the user sees their
        # state when Tab swaps them in.
        target = self.live_region_widgets.get(creature_id)
        prev = self.live_region
        if target is not None and target is not prev:
            self.live_region = target
        # Route scrollback commits emitted DURING this dispatch into
        # this creature's bucket (not the focused creature's). Without
        # this swap, bob's tool_done panel would replay under alice
        # the next time she Tabs back.
        prev_capture_target: str | None = None
        capture_swapped = False
        try:
            prev_capture_target = self.committer._capture_target
            if prev_capture_target != creature_id:
                self.committer.set_capture_target(creature_id)
                capture_swapped = True
        except Exception:
            pass
        try:
            if kind == "text":
                text = payload.get("text", "")
                state.append_text(text)
                try:
                    self.on_text_chunk(text)
                except Exception as e:  # pragma: no cover - defensive
                    logger.debug("on_text_chunk failed", error=str(e))
            elif kind == "processing_start":
                try:
                    self.on_processing_start()
                except Exception:
                    pass
            elif kind == "processing_end":
                try:
                    self.on_processing_end()
                except Exception:
                    pass
            elif kind == "activity":
                try:
                    self.on_activity_with_metadata(
                        payload.get("activity_type", ""),
                        payload.get("detail", ""),
                        payload.get("metadata", {}),
                    )
                except Exception:
                    pass
        finally:
            self.live_region = prev
            if capture_swapped:
                try:
                    self.committer.set_capture_target(prev_capture_target)
                except Exception:
                    pass
        state.record_event(payload)
        if is_focus:
            state.reset_unread()
        else:
            self._invalidate()

    def open_agent_overlay(self) -> None:
        if self.agent_overlay is None:
            return
        self.agent_overlay.open()
        self._invalidate()

    def roster_visible(self) -> bool:
        """True when the one-line roster row should be drawn."""
        if not self.multi_creature_enabled or self.roster is None:
            return False
        if (
            self.bus_overlay.visible
            or self.model_picker.visible
            or self.module_picker.visible
            or self.settings_overlay.visible
            or (self.agent_overlay is not None and self.agent_overlay.visible)
        ):
            return False
        return True

    def roster_ansi(self, width: int) -> str:
        """Render the roster Rich Text as ANSI for FormattedTextControl."""
        if self.roster is None:
            return ""
        text = self.roster.render(width)
        with self._scroll_console.capture() as cap:
            self._scroll_console.print(text, end="", width=width)
        return cap.get()

    def agent_overlay_ansi(self, width: int) -> str:
        """Render the agent overlay's Rich renderable as ANSI."""
        if self.agent_overlay is None:
            return ""
        renderable = self.agent_overlay.render()
        if renderable is None:
            return ""
        with self._scroll_console.capture() as cap:
            self._scroll_console.print(renderable, end="", width=width)
        return cap.get()

    # ── B2 redraw: clear scrollback + replay focused log ────────────

    # ANSI: clear scrollback (xterm 3J) + home cursor (H) + clear screen (2J).
    _CLEAR_SCROLLBACK = "\x1b[3J\x1b[H\x1b[2J"

    def redraw_focused(self) -> None:
        """Wipe terminal scrollback and re-emit the focused creature's log.

        Used on focus change so the user sees ONLY the focused
        creature's history (B2 design — single shared terminal, redraw
        on switch). The replay runs with the committer's ``_replaying``
        flag set so the re-emitted commits don't get re-captured into
        the bucket they came from.
        """
        if not self.multi_creature_enabled:
            return
        cid = self.focus_controller.focus_id
        if not cid:
            return
        try:
            self.committer.ansi(self._CLEAR_SCROLLBACK)
        except Exception:
            pass
        # Reset whitespace state so the first replayed item gets its
        # leading blank from a fresh "scrollback is empty" baseline.
        try:
            self.committer._last_was_blank = True
            self.committer._pending_block_close = False
        except Exception:
            pass
        # Reprint the banner so the redrawn screen has identity context
        # (creature name + model) at the top instead of an empty void.
        try:
            self._print_banner()
        except Exception:
            pass
        events = self.committer.captured_for(cid)
        if not events:
            return
        try:
            self.committer.set_replay_mode(True)
            for method_name, args in events:
                method = getattr(self.committer, method_name, None)
                if method is None:
                    continue
                try:
                    method(*args)
                except Exception as e:  # pragma: no cover - defensive
                    logger.debug(
                        "replay step failed",
                        method=method_name,
                        error=str(e),
                    )
        finally:
            self.committer.set_replay_mode(False)

    # ── User-message capture helpers (@name + broadcast) ───────────

    def commit_user_message_for(self, creature_id: str, text: str) -> None:
        """Display ``text`` as a user message and record it under ``creature_id``.

        Used by ``@name`` retargeting so the displayed input lives in
        the recipient's bucket rather than the focused creature's —
        otherwise Tab-to-recipient would show only the answer, with
        the question stranded back in the sender's view.
        """
        prev: str | None = None
        try:
            prev = self.committer._capture_target
            self.committer.set_capture_target(creature_id)
        except Exception:
            pass
        try:
            self._commit_user_message(text)
        finally:
            try:
                self.committer.set_capture_target(prev)
            except Exception:
                pass

    def commit_user_message_broadcast(self, text: str) -> None:
        """Display once + capture into every creature's bucket."""
        if not self.multi_creature_enabled or self.engine is None:
            self._commit_user_message(text)
            return
        # Visible commit goes through the focused bucket as usual.
        self._commit_user_message(text)
        # Then record a duplicate into every OTHER creature's bucket
        # so each redraw shows the broadcast in its own history.
        focused = self.focus_controller.focus_id
        for c in self.engine.list_creatures():
            cid = c.creature_id
            if cid == focused:
                continue
            try:
                self.committer._captures.setdefault(cid, []).append(
                    ("user_message", (text,))
                )
            except Exception:
                pass

    # ── Runtime graph change handling ────────────────────────────────

    def mount_creature_sink(self, creature: Any) -> None:
        """Mount the multiplex sink for ``creature``, saving the previous.

        Idempotent — re-mount on a creature already managed by us is a
        no-op (the saved ``_managed_outputs`` entry from the first mount
        survives). Called once per creature by
        ``run_engine_with_rich_cli`` at boot and again by
        :meth:`_on_creature_started` for runtime spawns.
        """
        if not self.multi_creature_enabled:
            return
        cid = creature.creature_id
        router = getattr(creature.agent, "output_router", None)
        if router is None:
            return
        if cid in self._managed_outputs:
            return
        self._managed_outputs[cid] = router.default_output
        router.default_output = MultiplexedRichOutput(
            handler=self._handle_creature_event,
            creature_id=cid,
            creature_name=creature.name or cid,
        )

    def restore_creature_sink(self, creature_id: str) -> None:
        """Reverse of :meth:`mount_creature_sink` for a single creature."""
        if creature_id not in self._managed_outputs:
            return
        prev = self._managed_outputs.pop(creature_id)
        if self.engine is None:
            return
        try:
            c = self.engine.get_creature(creature_id)
        except Exception:
            return
        router = getattr(c.agent, "output_router", None)
        if router is None:
            return
        router.default_output = prev

    def start_engine_watch(self) -> None:
        """Spawn the engine-subscription task. Idempotent.

        Called from ``run_engine_with_rich_cli`` after the focus
        creature's ``start()`` so the listener never fires before the
        app's per-creature dicts are fully populated.
        """
        if not self.multi_creature_enabled or self.engine is None:
            return
        if self._engine_watch_task is not None and not self._engine_watch_task.done():
            return
        self._engine_watch_task = asyncio.create_task(self._watch_engine())

    async def _watch_engine(self) -> None:
        """Subscribe to engine events and dispatch topology changes."""
        if self.engine is None:
            return
        filt = EventFilter(
            kinds={EventKind.CREATURE_STARTED, EventKind.CREATURE_STOPPED}
        )
        try:
            async for ev in self.engine.subscribe(filt):
                try:
                    if ev.kind == EventKind.CREATURE_STARTED:
                        self._on_creature_started(ev.creature_id or "")
                    elif ev.kind == EventKind.CREATURE_STOPPED:
                        self._on_creature_stopped(ev.creature_id or "")
                except Exception as e:  # pragma: no cover - defensive
                    logger.exception("topology event dispatch failed", error=str(e))
        except asyncio.CancelledError:
            return
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("engine subscribe loop crashed", error=str(e), exc_info=True)

    def _on_creature_started(self, creature_id: str) -> None:
        """A creature was added — wire it into the rich CLI surface."""
        if not creature_id or self.engine is None:
            return
        try:
            creature = self.engine.get_creature(creature_id)
        except Exception:
            return
        first_arrival = not self.focus_controller.creature_ids
        self._install_creature_slot(creature, is_focus=first_arrival)
        self.mount_creature_sink(creature)
        self.focus_controller.add(creature_id)
        if first_arrival:
            self._on_focus_changed("", creature_id)
        self._invalidate()

    def _on_creature_stopped(self, creature_id: str) -> None:
        """A creature was removed — restore its sink and drop state."""
        if not creature_id:
            return
        self.restore_creature_sink(creature_id)
        was_focus = self.focus_controller.focus_id == creature_id
        old_focus = self.focus_controller.focus_id
        new_focus = self.focus_controller.remove(creature_id)
        self.live_regions.pop(creature_id, None)
        self.draft_by_creature.pop(creature_id, None)
        try:
            self.committer.clear_capture(creature_id)
        except Exception:
            pass
        widget = self.live_region_widgets.pop(creature_id, None)
        # If the removed creature owned the active live_region pointer,
        # repoint at the new focus's widget (or a blank one when the
        # roster emptied out — keeps ``self.live_region`` non-None so
        # downstream code never has to None-check it).
        if was_focus and widget is self.live_region:
            if new_focus:
                self._on_focus_changed(old_focus, new_focus)
            else:
                self.live_region = LiveRegion()
        self._invalidate()

    async def teardown_multi_creature(self) -> None:
        """Cancel the engine watcher and restore every managed sink.

        Called from ``run_engine_with_rich_cli`` in its ``finally``
        block. Safe to call when never set up — it is a no-op for the
        single-creature path.
        """
        task = self._engine_watch_task
        self._engine_watch_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if not self._managed_outputs:
            return
        for cid in list(self._managed_outputs.keys()):
            self.restore_creature_sink(cid)

    async def dispatch_topology_command(self, name: str, args: str) -> bool:
        """Run a topology-aware command locally with the right context.

        Returns ``True`` if the command was handled (caller should
        return) and ``False`` if the caller should fall through to
        the agent-level dispatcher.
        """
        if not self.multi_creature_enabled or name not in _TOPOLOGY_COMMANDS:
            return False
        cmd = self._command_registry.get(name)
        if cmd is None:
            return False
        ctx = UserCommandContext(
            agent=self.agent,
            session=getattr(self.agent, "session", None),
            extra={
                "engine": self.engine,
                "creature_id": self.focus_controller.focus_id,
            },
        )
        try:
            result = await cmd.execute(args, ctx)
        except Exception as e:
            self._commit_text(f"[red]Command error:[/red] {e}")
            return True
        if result is None:
            self._commit_text(f"[red]Unknown command:[/red] /{name}")
        elif result.error:
            self._commit_text(f"[red]{result.error}[/red]")
        elif result.output:
            self._commit_text(result.output)
        return True


__all__ = ["AppMultiCreatureMixin"]
