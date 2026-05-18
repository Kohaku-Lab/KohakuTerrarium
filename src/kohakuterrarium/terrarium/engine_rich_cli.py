"""Engine launcher for ``--mode cli`` (the rich inline CLI).

The pre-unification rich CLI mode was removed in commit ab256f72 with a
"deferred" placeholder warning ("``cli`` / ``plain`` variants will
return in a follow-up"). This module is that follow-up: it mounts
:class:`RichCLIApp` on top of a running :class:`Terrarium` engine so
``kt run --mode cli`` produces an inline prompt with bordered input
+ live region instead of the full-screen Textual TUI.

The shape mirrors :func:`terrarium.engine_cli.run_engine_with_tui`,
with one critical addition — the focus creature's input and output
modules are conditionally swapped to the rich-CLI pair:

- **Output** is replaced whenever the rich CLI is mounted: that is the
  whole point of ``--mode cli``. The engine's own teardown talks to the
  restored previous output, so the app's :class:`RichCLIOutput`
  disappears with the app.
- **Input** is replaced *only when the configured module would fight
  prompt_toolkit for stdin*. The rich CLI grabs stdin in raw mode; an
  input like :class:`CLIInput` running concurrently spawns a blocking
  ``sys.stdin.readline()`` in an executor thread that races
  prompt_toolkit for every byte (no key binding fires reliably,
  Ctrl+C/Ctrl+D get eaten). Non-terminal inputs (NoneInput, Discord,
  webhooks, custom polling) leave well alone — the user explicitly
  opted into ``--mode cli`` for the on-screen composer; they did not
  ask to silence their Discord bot.

The input swap is only safe **before** the creature starts, because
once started its input is parked inside a long-running coroutine that
in the CLIInput case has already spawned an unkillable executor
thread. ``cli/run.py`` defers ``add_creature(start=False)`` for the
solo ``--mode cli`` path so this function can perform the swap and
then call ``focus_creature.start()``.

Limitations of the rich CLI surface (single-stream, no tabs):

- Sibling creatures in a multi-creature graph keep running but their
  output does not surface here. Use the TUI (default) for those.
- Channel transcripts do not render. Same reason.

These are intentional — the rich CLI is the focused single-creature
experience the user explicitly opted into via ``--mode cli``. Pick
the TUI when topology visibility matters.
"""

import asyncio

from kohakuterrarium.builtins.cli_rich.app import RichCLIApp
from kohakuterrarium.builtins.cli_rich.input import RichCLIInput
from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput
from kohakuterrarium.builtins.inputs.cli import CLIInput, NonBlockingCLIInput
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _input_conflicts_with_terminal(input_module: InputModule) -> bool:
    """True if ``input_module`` reads stdin / owns the terminal.

    The rich CLI mounts a prompt_toolkit ``Application`` that grabs
    stdin in raw mode. Any input module already reading stdin (via
    ``sys.stdin.readline`` in an executor thread, or via Textual)
    races prompt_toolkit for bytes — no key binding fires reliably
    and Ctrl+C / Ctrl+D get eaten by the wrong reader.

    Non-terminal inputs (NoneInput, Discord, webhook listeners,
    user-defined polling inputs) coexist with prompt_toolkit just
    fine and are left in place.
    """
    if isinstance(input_module, (CLIInput, NonBlockingCLIInput)):
        return True
    # Lazy import — TUIInput pulls in Textual. Avoid loading it
    # unnecessarily on minimal installs.
    try:
        from kohakuterrarium.builtins.tui.input import TUIInput
    except Exception:
        return False
    return isinstance(input_module, TUIInput)


async def run_engine_with_rich_cli(
    engine: Terrarium,
    focus_creature_id: str,
    store: SessionStore | None = None,
) -> None:
    """Run the rich inline CLI against the engine's creatures.

    Single-creature topology behaves identically to the 1.4 path:
    only the focus creature is wired, only its output reaches the
    terminal. Multi-creature topology activates topic 08's surface —
    a roster row above the input, focus switching via Tab/Shift+Tab,
    ``@name`` retargeting, Ctrl+A overlay, topology-aware slash
    commands (``/stop`` / ``/start`` / ``/jobs`` / ``/channels`` /
    ``/scratchpad`` / ``/spawn``).

    Input is still swapped only for the focus creature (stdin can't
    be shared); every other creature gets a :class:`MultiplexedRichOutput`
    sink that stamps events with its creature_id and routes them to
    ``app._handle_creature_event``.
    """
    focus_creature = engine.get_creature(focus_creature_id)
    agent = focus_creature.agent
    all_creatures = list(engine.list_creatures())
    is_multi = len(all_creatures) > 1

    previous_input = agent.input

    swap_input = not focus_creature.is_running and _input_conflicts_with_terminal(
        previous_input
    )
    if swap_input:
        agent.input = RichCLIInput()
        logger.debug(
            "Rich CLI swapped focus creature input",
            previous=type(previous_input).__name__,
            creature_id=focus_creature_id,
        )

    app = RichCLIApp(agent)

    if is_multi:
        # The app's setup populates ``live_regions`` + ``focus_controller``
        # before any sink is mounted, so the very first event finds a
        # ready state slot. Sink mounting goes through the mixin so it
        # records the previous sink in ``_managed_outputs`` — the same
        # path runtime spawns take.
        app.setup_multi_creature(engine, focus_creature_id)
        for c in all_creatures:
            app.mount_creature_sink(c)
    else:
        rich_output = RichCLIOutput(app)
        agent.output_router.default_output = rich_output

    if not focus_creature.is_running:
        await focus_creature.start()

    if is_multi:
        # Engine subscription starts after the focus creature is up so
        # the watcher never observes a creature that didn't yet have a
        # widget slot allocated.
        app.start_engine_watch()

    pending = getattr(agent, "_pending_resume_events", None)
    if pending:
        try:
            app.replay_session(pending)
        except Exception as exc:
            logger.debug(
                "Rich CLI session replay failed", error=str(exc), exc_info=True
            )
        agent._pending_resume_events = None

    try:
        await app.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if is_multi:
            try:
                await app.teardown_multi_creature()
            except Exception as exc:
                logger.debug(
                    "Rich CLI multi-creature teardown failed",
                    error=str(exc),
                    exc_info=True,
                )
        if swap_input:
            # ``previous_input`` was never started (we deferred
            # creature.start() until after the swap). Restoring the
            # reference is enough — the engine's teardown calls
            # ``previous_input.stop()`` which is idempotent on a
            # never-started module.
            agent.input = previous_input
        if store is not None:
            try:
                store.flush()
            except Exception as exc:
                logger.debug(
                    "Rich CLI session store flush failed",
                    error=str(exc),
                    exc_info=True,
                )
