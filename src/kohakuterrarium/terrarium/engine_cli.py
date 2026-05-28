"""Engine TUI launcher.

Mounts the Textual-based TUI on top of a running :class:`Terrarium`
engine. ``run_engine_with_tui`` is the single entry point shared
between ``kt run creature.yaml`` (solo creature), ``kt run
terrarium.yaml`` (recipe), and ``kt resume``. The TUI is uniform
across all three — there is no creature-vs-terrarium fork at the
runtime layer. Solo sessions are graphs with one creature; the same
tab strip + channel-tab plumbing applies.

The TUI tabs are: focus creature first, then every other creature in
the graph, then one ``#channel`` tab per shared channel. The TUI
subscribes to engine topology events so creatures spawned at runtime
(via ``group_add_node``) and channels created at runtime (via
``group_channel(action="create")``) are surfaced as new tabs without
the user having to restart.
"""

import asyncio
import shlex
from collections.abc import Iterable
from typing import Any

from kohakuterrarium.builtins.inputs.cli import CLIInput, NonBlockingCLIInput
from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.builtins.tui.input import TUIInput
from kohakuterrarium.builtins.tui.output import TUIOutput
from kohakuterrarium.builtins.tui.session import TUISession
from kohakuterrarium.builtins.tui.widgets import ChatInput
from kohakuterrarium.core.session import get_session
from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.core.channel import BaseChannel, ChannelMessage
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    parse_slash_command,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.utils.logging import get_logger, restore_logging, suppress_logging

logger = get_logger(__name__)


async def _handle_tui_slash(text: str, tui: "TUISession", focus: Any) -> bool:
    """Dispatch TUI-native slash commands to Textual modals.

    Returns True if the slash was handled (caller should ``continue``);
    False to fall through to the standard agent slash dispatch.

    Mirrors ``cli_rich/app.py:RichCLIApp._handle_slash`` for TUI mode.
    The old design routed this via ``TUIInput.try_user_command`` →
    ``_intercept_module`` / ``_intercept_model``, which required the
    agent's input to BE TUIInput. With the input swap to NoneInput
    (stdin-contention fix), that path is gone — so the dispatch moves
    here, the runner-level loop where the engine already has the
    TUISession + focus agent in scope.
    """
    name, args = parse_slash_command(text)
    stripped_args = (args or "").strip()

    if name == "model" and not stripped_args:
        await tui.show_model_picker_modal(focus)
        return True

    if name in ("module", "modules", "mod"):
        try:
            tokens = shlex.split(stripped_args)
        except ValueError:
            tokens = []
        sub = tokens[0].lower() if tokens else "list"
        if sub in ("", "list"):
            await tui.show_modules_modal(focus)
            return True
        if sub == "edit" and len(tokens) > 1:
            opened = await tui.show_module_edit_modal(focus, tokens[1])
            if opened:
                return True
            # Fall through if name didn't resolve — text command will
            # print the "not found" / "ambiguous" error.

    # Other slash commands fall through to the agent's standard
    # slash dispatch via ``focus.inject_input``.
    return False


def _input_steals_stdin(input_module: InputModule) -> bool:
    """True if ``input_module`` reads stdin and would race Textual.

    The TUI mounts Textual's ``App`` which grabs stdin in raw mode +
    mouse capture. Any input module already reading stdin (CLIInput's
    blocking ``sys.stdin.readline`` in an executor thread, the
    NonBlockingCLIInput select() poller, or another TUIInput) consumes
    bytes BEFORE Textual sees them — clicks, keys, and Ctrl+C all get
    eaten by the wrong reader. Result: the TUI renders the layout but
    is otherwise inert (user-reported "TUI fully not usable" freeze).

    Non-terminal inputs (NoneInput, Discord, webhook listeners,
    user-defined polling inputs) coexist with Textual fine and stay.
    """
    return isinstance(input_module, (CLIInput, NonBlockingCLIInput, TUIInput))


def wire_channel_registry_callbacks(
    channels: Iterable[BaseChannel], tui: "TUISession"
) -> None:
    for ch in channels:
        ch_name = ch.name

        def _make_ch_cb(channel_name: str):
            def _cb(cn: str, message) -> None:
                sender = message.sender if hasattr(message, "sender") else ""
                content = (
                    message.content if hasattr(message, "content") else str(message)
                )
                tui.add_trigger_message(
                    f"[{channel_name}] {sender}",
                    str(content)[:500],
                    target=f"#{channel_name}",
                )

            return _cb

        ch.on_send(_make_ch_cb(ch_name))


async def run_engine_with_tui(
    engine: Terrarium,
    focus_creature_id: str,
    store: SessionStore | None = None,
    *,
    handle_command=None,
) -> None:
    """Run the engine TUI with focus on ``focus_creature_id``.

    The focus creature is the one whose tab the TUI opens to and whose
    inputs route from the user prompt by default. For solo ``kt run``
    this is the lone creature; for a recipe it's the privileged root.
    """
    focus_creature = engine.get_creature(focus_creature_id)
    focus = focus_creature.agent
    graph_id = focus_creature.graph_id
    graph = engine.get_graph(graph_id)
    env = engine._environments[graph_id]

    # Stdin-stealing input swap (matches the rich-CLI runner's pattern).
    # Textual's App grabs stdin in raw mode + mouse capture. If the
    # focus creature's configured input is CLIInput / NonBlockingCLIInput
    # / TUIInput, those would race Textual for stdin bytes and the
    # entire Textual interface ends up inert (user-reported "fully not
    # interactable, can't click anything, Ctrl+C doesn't exit"). Swap
    # to NoneInput BEFORE the creature starts; this engine's main loop
    # drives input via ``tui.get_input()`` + ``focus.inject_input(...)``
    # so the agent doesn't need its own stdin reader.
    swapped_inputs: list[tuple[Any, InputModule]] = []
    if not focus_creature.is_running and _input_steals_stdin(focus.input):
        original = focus.input
        focus.input = NoneInput()
        focus._init_user_commands()
        swapped_inputs.append((focus, original))
        logger.debug(
            "TUI swapped focus creature input",
            previous=type(original).__name__,
            creature_id=focus_creature_id,
        )
    # Sibling creatures in a multi-creature graph: same hazard. If any
    # sibling is still running its CLIInput from the engine's earlier
    # ``creature.start()`` we leave it alone (already racing — too late
    # to swap without disrupting in-flight state); but un-started
    # siblings get the same NoneInput swap.
    for creature in engine.list_creatures():
        if creature.creature_id == focus_creature_id:
            continue
        if creature.graph_id != graph_id:
            continue
        if not creature.is_running and _input_steals_stdin(creature.agent.input):
            original = creature.agent.input
            creature.agent.input = NoneInput()
            creature.agent._init_user_commands()
            swapped_inputs.append((creature.agent, original))

    graph_creatures = [engine.get_creature(cid) for cid in graph.creature_ids]
    tui_tabs = [focus_creature_id]
    tui_tabs.extend(
        c.creature_id for c in graph_creatures if c.creature_id != focus_creature_id
    )
    tui_tabs.extend(f"#{ch_info.name}" for ch_info in graph.channels.values())

    tui = TUISession(agent_name=graph_id)
    tui.set_terrarium_tabs(tui_tabs)

    focus_output = TUIOutput(session_key=focus_creature_id)
    focus_output._tui = tui
    focus_output._running = True
    focus_output._default_target = focus_creature_id
    focus.output_router.default_output = focus_output

    routed_creatures: set[str] = {focus_creature_id}
    for creature in graph_creatures:
        if creature.creature_id == focus_creature_id:
            continue
        creature_out = TUIOutput(session_key=creature.creature_id)
        creature_out._tui = tui
        creature_out._running = True
        creature_out._default_target = creature.creature_id
        creature.agent.output_router.default_output = creature_out
        routed_creatures.add(creature.creature_id)

    tui.on_cancel_job = focus._cancel_job
    tui.on_promote_job = focus._promote_handle
    # Publish the focus agent on the TUISession so F2 / F3 modal
    # handlers can reach it. Without this, ``AgentTUI.action_open_modules``
    # / ``action_open_model_picker`` read ``self.tui_session.host_agent``
    # and short-circuit on ``None`` (silent return — keys appear dead).
    # ``TUIInput.set_user_commands`` normally sets this; we swapped to
    # ``NoneInput`` to avoid stdin contention, so engine_cli takes over.
    tui.host_agent = focus

    # Publish this TUISession under each routed creature's session_key
    # BEFORE the creatures start. Otherwise ``TUIOutput._on_start``
    # (fired on the first ``output_router.start()``) does
    # ``if session.tui is None: session.tui = TUISession(...)`` and
    # creates a SECOND session, then overwrites the ``_tui`` we just
    # wired by hand. Result: agent output streams into the secondary
    # session that has no AgentTUI mounted — user sees the layout but
    # nothing the agent emits ever lands on screen, which they reported
    # as "TUI input never triggers agent". Pre-publishing makes
    # ``_on_start`` find the session already populated and reuse it.
    for creature in engine.list_creatures():
        if creature.graph_id != graph_id:
            continue
        session_for_creature = get_session(creature.creature_id)
        session_for_creature.tui = tui

    # Start any creatures whose input we just swapped — same deferred-
    # start pattern the rich-CLI runner uses (engine_rich_cli.py:140).
    # Done BEFORE ``tui.start()`` so the agent is up by the time Textual
    # begins routing user input to ``focus.inject_input``.
    for creature in engine.list_creatures():
        if creature.graph_id == graph_id and not creature.is_running:
            await creature.start()

    await tui.start()
    # Wire ESC → interrupt AFTER tui.start() creates the AgentTUI.
    # The previous wiring ran BEFORE tui.start() when ``tui._app`` was
    # still None, so the ``if tui._app:`` guard made it a permanent
    # no-op and ESC went nowhere. With the app now live, set the
    # callback directly on it.
    if tui._app:
        tui._app.on_interrupt = focus.interrupt
    suppress_logging()
    app_task = asyncio.create_task(tui.run_app())
    await tui.wait_ready()

    _update_session_info(tui, focus, graph_id, store)
    _update_terrarium_panel(tui, graph_creatures, env, focus_creature_id)
    wired_channels: set[str] = set()
    _wire_new_channels(env, tui, wired_channels)
    refresh_task = asyncio.create_task(
        _refresh_tui_on_topology_change(
            engine,
            tui,
            graph_id,
            focus_creature_id,
            wired_channels,
            routed_creatures,
        )
    )

    commands = {n: get_builtin_user_command(n) for n in list_builtin_user_commands()}
    aliases = _build_command_aliases(commands)
    cmd_context = UserCommandContext(agent=focus, session=focus.session)
    cmd_context.extra["command_registry"] = commands
    _set_command_hints(tui, commands)

    # Track in-flight inject_input tasks so we can drain them on exit
    # but DON'T await them inline. Awaiting inline blocks the input
    # loop for the whole turn — the second user message piles up in
    # ``_input_queue`` and can't reach ``inject_input`` until the
    # first turn returns. That defeats mid-turn injection: the agent's
    # ``_pending_mid_turn_inputs`` buffer never sees the second
    # message because no second ``inject_input`` is fired while the
    # first turn holds the lock. Firing as a task hands the second
    # call into the agent immediately; ``_process_event`` detects the
    # lock is held and buffers it for the current turn's drain.
    inflight_inputs: list[asyncio.Task] = []

    def _spawn_inject(coro) -> None:
        task = asyncio.create_task(coro)
        inflight_inputs.append(task)
        # Reap finished tasks so the list doesn't grow forever.
        inflight_inputs[:] = [t for t in inflight_inputs if not t.done()]

    try:
        while True:
            text = await tui.get_input()
            if not text:
                break
            if text.startswith("/") and handle_command is not None:
                cmd_result = await handle_command(
                    text, tui, commands, aliases, cmd_context, None
                )
                if cmd_result is False:
                    break
                if cmd_result is True:
                    continue
            # TUI-native slash modals — mirror the Rich CLI dispatch in
            # ``cli_rich/app.py:_handle_slash``. Awaited inline because
            # modals must finish before the input loop reads the next
            # text (the modal IS the response to this input).
            if text.startswith("/"):
                if await _handle_tui_slash(text, tui, focus):
                    continue
            active_tab = tui.get_active_tab()
            if not active_tab or active_tab == focus_creature_id:
                tui.set_active_target(focus_creature_id)
                _spawn_inject(focus.inject_input(text, source="tui"))
            elif active_tab.startswith("#"):
                await _send_to_channel_tab(tui, env, active_tab, text)
            else:
                tui.set_active_target(active_tab)
                _spawn_inject(
                    focus.inject_input(
                        f"Send this to {active_tab}: {text}", source="tui"
                    )
                )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        restore_logging()
        refresh_task.cancel()
        try:
            await refresh_task
        except (asyncio.CancelledError, Exception):
            pass
        app_task.cancel()
        try:
            await app_task
        except (asyncio.CancelledError, Exception):
            pass
        tui.stop()
        # Restore original input modules (the swapped NoneInput was a
        # transient stub for the TUI session only — same convention as
        # ``engine_rich_cli.run_engine_with_rich_cli``).
        for agent, original in swapped_inputs:
            agent.input = original


def _update_session_info(
    tui: TUISession, focus, graph_id: str, store: SessionStore | None
) -> None:
    model = getattr(focus.llm, "model", "") or getattr(
        getattr(focus.llm, "config", None), "model", ""
    )
    session_id = ""
    if store:
        try:
            meta = store.load_meta()
            session_id = meta.get("session_id", "")
        except Exception as e:
            logger.warning(
                "Failed to load session meta for TUI", error=str(e), exc_info=True
            )
    tui.update_session_info(session_id=session_id, model=model, agent_name=graph_id)
    compact_mgr = getattr(focus, "compact_manager", None)
    if compact_mgr:
        max_ctx = compact_mgr.config.max_tokens
        compact_at = int(max_ctx * compact_mgr.config.threshold) if max_ctx else 0
        tui.set_context_limits(max_ctx, compact_at)


def _update_terrarium_panel(
    tui: TUISession, graph_creatures, env, focus_creature_id: str
) -> None:
    creature_info = [
        {
            "name": creature.creature_id,
            "running": creature.is_running,
            "listen": creature.listen_channels,
            "send": creature.send_channels,
        }
        for creature in graph_creatures
        if creature.creature_id != focus_creature_id
    ]
    tui.update_terrarium(creature_info, env.shared_channels.get_channel_info())


def _build_command_aliases(commands: dict) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for name, cmd in commands.items():
        for alias in getattr(cmd, "aliases", []):
            aliases[alias] = name
    return aliases


def _set_command_hints(tui: TUISession, commands: dict) -> None:
    if not tui._app:
        return
    try:
        inp = tui._app.query_one("#input-box", ChatInput)
        inp.command_names = list(commands.keys())
    except Exception as e:
        logger.warning(
            "Failed to set command hints on TUI input", error=str(e), exc_info=True
        )


def _wire_new_channels(env, tui: "TUISession", wired: set[str]) -> None:
    """Install on_send callbacks on every channel not already wired.

    Called once at startup and again on every topology change so
    channels added at runtime (via ``group_channel(action="create")``)
    show up as transcript-emitting tabs without a TUI restart.
    ``wired`` is mutated in place so re-entry is idempotent.
    """
    for ch in env.shared_channels._channels.values():
        if ch.name in wired:
            continue
        wire_channel_registry_callbacks([ch], tui)
        wired.add(ch.name)


async def _refresh_tui_on_topology_change(
    engine: Terrarium,
    tui: "TUISession",
    graph_id: str,
    focus_creature_id: str,
    wired_channels: set[str],
    routed_creatures: set[str],
) -> None:
    """Re-render the tab strip on every topology change in our graph.

    Subscribes to ``CREATURE_STARTED`` / ``CREATURE_STOPPED`` /
    ``TOPOLOGY_CHANGED`` (which fires on add/remove channel and on
    cross-graph wires) so a creature spawning a peer mid-conversation
    surfaces as a new tab on the next event tick. Channel callbacks
    are also re-wired so the new ``#channel`` tab actually renders
    incoming sends.
    """
    filt = EventFilter(
        kinds={
            EventKind.CREATURE_STARTED,
            EventKind.CREATURE_STOPPED,
            EventKind.TOPOLOGY_CHANGED,
            EventKind.SESSION_KIND_CHANGED,
        }
    )
    try:
        async for _ev in engine.subscribe(filt):
            graph = engine._topology.graphs.get(graph_id)
            if graph is None:
                continue
            env = engine._environments.get(graph_id)
            if env is None:
                continue
            graph_creatures = []
            for cid in graph.creature_ids:
                try:
                    graph_creatures.append(engine.get_creature(cid))
                except KeyError:
                    continue
            tabs = [focus_creature_id]
            tabs.extend(
                c.creature_id
                for c in graph_creatures
                if c.creature_id != focus_creature_id
            )
            tabs.extend(f"#{name}" for name in graph.channels)
            try:
                tui.set_terrarium_tabs(tabs)
            except Exception as exc:
                logger.warning("TUI tab refresh failed", error=str(exc), exc_info=True)
            _update_terrarium_panel(tui, graph_creatures, env, focus_creature_id)
            _wire_new_channels(env, tui, wired_channels)
            for creature in graph_creatures:
                if creature.creature_id in routed_creatures:
                    continue
                creature_out = TUIOutput(session_key=creature.creature_id)
                creature_out._tui = tui
                creature_out._running = True
                creature_out._default_target = creature.creature_id
                creature.agent.output_router.default_output = creature_out
                routed_creatures.add(creature.creature_id)
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("topology subscriber crashed", error=str(exc), exc_info=True)


async def _send_to_channel_tab(
    tui: TUISession, env, active_tab: str, text: str
) -> None:
    ch_name = active_tab[1:]
    channel = env.shared_channels.get(ch_name)
    if channel is None:
        tui.add_trigger_message(
            "[error]",
            f"Channel '{ch_name}' not found",
            target=active_tab,
        )
        return
    tui.add_user_message(text, target=active_tab)
    await channel.send(ChannelMessage(sender="human", content=text))
