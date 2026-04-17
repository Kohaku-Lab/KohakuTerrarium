"""CLI resume command — resume an agent or terrarium from a session file."""

import asyncio
import sys

from kohakuterrarium.cli.run import _resolve_session, _run_agent_rich_cli
from kohakuterrarium.session.resume import (
    detect_session_type,
    resume_agent,
    resume_terrarium,
)
from kohakuterrarium.terrarium.cli import run_terrarium_with_tui
from kohakuterrarium.utils.logging import enable_stderr_logging, set_level


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

    session_type = detect_session_type(path)
    store = None

    try:
        if session_type == "terrarium":
            # Don't pass io_mode - terrarium CLI controls all I/O
            runtime, store = resume_terrarium(path, pwd_override)
            asyncio.run(run_terrarium_with_tui(runtime))
        else:
            agent, store = resume_agent(
                path, pwd_override, io_mode=io_mode, llm_override=llm_override
            )
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
            print(f"\nSession saved. To resume:")
            print(f"  kt resume {path.stem}")
