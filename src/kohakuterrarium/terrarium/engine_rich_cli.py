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
    """Run the rich inline CLI against the engine's focus creature.

    For the solo ``kt run --mode cli`` path the caller has deferred
    ``creature.start()`` so we can swap the focus creature's IO
    modules first; we start the creature here once IO is wired. For
    paths where the focus creature is already running (recipes —
    which build the focus with ``NoneInput`` so there's no stdin
    conflict) we leave the input alone and only attach the rich
    output sink.
    """
    focus_creature = engine.get_creature(focus_creature_id)
    agent = focus_creature.agent

    previous_input = agent.input
    previous_output = agent.output_router.default_output

    # Input swap is only safe *before* the creature starts (see module
    # docstring). For the already-running case we accept the configured
    # input as-is; in practice that's NoneInput (recipe focus) which
    # doesn't touch stdin.
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
    rich_output = RichCLIOutput(app)
    agent.output_router.default_output = rich_output

    if not focus_creature.is_running:
        await focus_creature.start()

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
        agent.output_router.default_output = previous_output
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
