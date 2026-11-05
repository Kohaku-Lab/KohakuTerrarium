"""Engine launcher for ``--mode ktui``: the kohakutui terminal front end.

Every creature in the focus creature's graph is attached to one :class:`KtuiShell`. The focus
creature's input is swapped for :class:`KtuiInput` before it starts when its configured input
reads the terminal; other inputs (Discord, webhooks) keep running. Pending resume events are
replayed into the focus view, and the session store is flushed on the way out.
"""

from kohakuterrarium.builtins.inputs.cli import CLIInput, NonBlockingCLIInput
from kohakuterrarium.builtins.ktui.config import KtuiConfig
from kohakuterrarium.builtins.ktui.input import KtuiInput
from kohakuterrarium.builtins.ktui.shell import KtuiShell
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.startup_trace import mark as mark_startup

logger = get_logger(__name__)


def _reads_terminal(input_module: InputModule) -> bool:
    if isinstance(input_module, (CLIInput, NonBlockingCLIInput)):
        return True
    return type(input_module).__name__ in ("TUIInput", "RichCLIInput")


async def run_engine_with_ktui(
    engine: Terrarium,
    focus_creature_id: str,
    store: SessionStore | None = None,
    renderer: str | None = None,
) -> None:
    """Runs ktui on the engine until the user leaves; ``renderer`` overrides the saved one."""
    config = KtuiConfig.load()
    if renderer:
        config.renderer = renderer
        config.normalized()

    focus = engine.get_creature(focus_creature_id)
    graph = [c for c in engine.list_creatures() if c.graph_id == focus.graph_id]
    agent = focus.agent
    previous_input = agent.input
    swap_input = not focus.is_running and _reads_terminal(previous_input)
    if swap_input:
        agent.input = KtuiInput()
        # commands were registered on the replaced input during agent init
        agent._init_user_commands()

    shell = KtuiShell(engine, focus_creature_id, config)
    for creature in [focus, *(c for c in graph if c is not focus)]:
        shell.attach(creature)

    # from here nothing prints over the UI: stray output and warnings go to the Logs tab
    shell.chrome.capture()
    try:
        if not focus.is_running:
            await engine.start(focus)
        mark_startup(
            "ktui_creature_started", surface="ktui", creature_id=focus_creature_id
        )

        # every attached creature's saved history goes into its own view: the focus one once
        # the terminal is ours, the others now (unfocused views keep it until shown)
        pending = getattr(agent, "_pending_resume_events", None)
        agent._pending_resume_events = None
        for entry in shell.entries.values():
            other = entry.agent
            history = getattr(other, "_pending_resume_events", None)
            if other is not agent and history:
                other._pending_resume_events = None
                await entry.output.on_resume(history)
        await shell.run(resume_events=pending or None)
    finally:
        shell.chrome.release()
        if swap_input:
            agent.input = previous_input
        if store is not None:
            try:
                store.flush()
            except Exception as exc:
                logger.warning(
                    "ktui session store flush failed", error=str(exc), exc_info=True
                )
