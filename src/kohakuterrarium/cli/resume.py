"""CLI resume command — terminal-side wiring around session resume.

The session-format migration announcement and any logic shared with
the HTTP route live in
:mod:`kohakuterrarium.studio.persistence.resume`; this module is
strictly the rich-CLI presentation layer.
"""

import asyncio
import sys

from kohakuterrarium.builtins.cli_rich.input import RichCLIInput
from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput
from kohakuterrarium.cli.run import _resolve_session, _run_agent_rich_cli
from kohakuterrarium.session.resume import detect_session_type, resume_agent
from kohakuterrarium.studio.persistence.resume import announce_migration_if_needed
from kohakuterrarium.terrarium.cli import run_terrarium_with_tui
from kohakuterrarium.terrarium.legacy_resume import resume_terrarium
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    set_level,
)


def resume_cli(
    query: str | None,
    pwd_override: str | None,
    log_level: str,
    last: bool = False,
    io_mode: str | None = None,
    llm_override: str | None = None,
    log_stderr: str = "auto",
) -> int:
    """Resume an agent or terrarium from a session file."""
    configure_utf8_stdio(log=True)
    set_level(log_level)

    # Resolve mode the same way ``kt run`` does — rich CLI on a TTY,
    # plain otherwise. Keeps resume behavior consistent with run.
    if io_mode is None:
        io_mode = "cli" if sys.stdout.isatty() else "plain"

    # Mirror logs to stderr when the terminal is not owned by a
    # full-screen UI. ``auto`` treats plain as free; cli/tui as taken.
    if log_stderr == "on" or (log_stderr == "auto" and io_mode not in {"cli", "tui"}):
        enable_stderr_logging(log_level)

    path = _resolve_session(query, last=last)
    if path is None:
        if query:
            print(f"No session found matching: {query}")
        else:
            print("No sessions found in ~/.kohakuterrarium/sessions/")
        return 1

    # Wave D: announce any pending upgrade before we open the store so
    # the user sees what's happening; resume itself performs the work.
    announce_migration_if_needed(path)

    session_type = detect_session_type(path)
    store = None

    try:
        if session_type == "terrarium":
            # Don't pass io_mode - terrarium CLI controls all I/O
            runtime, store = resume_terrarium(path, pwd_override)
            asyncio.run(run_terrarium_with_tui(runtime))
        else:
            # ``cli`` mode lives in builtins.cli_rich, which sits above
            # session/ in the layering — so the rich modules are built
            # here and passed in directly. Other modes are handled by
            # ``resume_agent`` itself via the ``io_mode`` shortcut.
            resume_kwargs: dict = {
                "pwd_override": pwd_override,
                "llm_override": llm_override,
            }
            if io_mode == "cli":
                resume_kwargs["input_module"] = RichCLIInput()
                resume_kwargs["output_module"] = RichCLIOutput(app=None)
            else:
                resume_kwargs["io_mode"] = io_mode
            agent, store = resume_agent(path, **resume_kwargs)
            # ``cli`` mode uses RichCLIApp.run() as the main loop, not
            # agent.run(). Without this dispatch, resume in CLI mode
            # blocks forever showing nothing because the agent is started
            # but no input/output frontend is actually running.
            if io_mode == "cli":
                asyncio.run(_run_agent_rich_cli(agent))
            else:
                asyncio.run(agent.run())
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        if store:
            store.close()
        if path.exists():
            print("\nSession saved. To resume:")
            print(f"  kt resume {path.stem}")
