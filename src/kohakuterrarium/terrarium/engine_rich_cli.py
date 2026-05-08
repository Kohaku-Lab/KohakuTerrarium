"""Engine launcher for ``--mode cli`` (the rich inline CLI).

The pre-unification rich CLI mode was removed in commit ab256f72 with a
"deferred" placeholder warning ("``cli`` / ``plain`` variants will
return in a follow-up"). This module is that follow-up: it mounts
:class:`RichCLIApp` on top of a running :class:`Terrarium` engine so
``kt run --mode cli`` produces an inline prompt with bordered input
+ live region instead of the full-screen Textual TUI.

The shape mirrors :func:`terrarium.engine_cli.run_engine_with_tui`:

- The engine owns lifecycle. The focus creature is already started by
  the time we mount the app; we don't call ``agent.start()`` /
  ``agent.stop()``.
- The focus creature's ``output_router.default_output`` is replaced
  with a :class:`RichCLIOutput` that drives the app's live region.
- Pending resume events (set by the resume path) are flushed to
  scrollback before the prompt-toolkit Application takes over the
  bottom of the terminal.

Limitations of the rich CLI surface (single-stream, no tabs):

- Sibling creatures in a multi-creature graph keep running but their
  output does not surface here. Use the TUI (default) for those.
- Channel transcripts do not render. Same reason.

These are intentional — the rich CLI is the focused single-creature
experience the user explicitly opted into via ``--mode cli``. Pick
the TUI when topology visibility matters.
"""

import asyncio

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.builtins.cli_rich.app import RichCLIApp
from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput

logger = get_logger(__name__)


async def run_engine_with_rich_cli(
    engine: Terrarium,
    focus_creature_id: str,
    store: SessionStore | None = None,
) -> None:
    """Run the rich inline CLI against the engine's focus creature.

    The engine has already started ``focus_creature`` by the time this
    is called (``engine.add_creature(start=True)`` / ``apply_recipe``).
    We attach a :class:`RichCLIOutput` sink, replay any pending resume
    events to scrollback, then enter the prompt-toolkit Application
    loop. On exit we restore the default output sink so the engine's
    own teardown isn't talking through a torn-down app.
    """
    focus_creature = engine.get_creature(focus_creature_id)
    agent = focus_creature.agent

    app = RichCLIApp(agent)
    rich_output = RichCLIOutput(app)
    previous_output = agent.output_router.default_output
    agent.output_router.default_output = rich_output

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
        if store is not None:
            try:
                store.flush()
            except Exception as exc:
                logger.debug(
                    "Rich CLI session store flush failed",
                    error=str(exc),
                    exc_info=True,
                )
