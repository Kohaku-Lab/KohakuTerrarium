"""CLI resume command — resume an agent or terrarium from a session file."""

import asyncio
import sys

from kohakuterrarium.cli.run import _resolve_session, _run_agent_rich_cli
from kohakuterrarium.session.migrations import (
    MAX_SUPPORTED_VERSION,
    discover_versions,
    path_for_version,
)
from kohakuterrarium.session.resume import (
    detect_session_type,
    resume_agent,
    resume_terrarium,
)
from kohakuterrarium.terrarium.cli import run_terrarium_with_tui
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    get_logger,
    set_level,
)

logger = get_logger(__name__)


def _announce_migration_if_needed(path) -> None:
    """Log an informational line when resume will trigger a migration.

    Doesn't perform the migration itself — that's the job of
    :func:`ensure_latest_version` inside resume. This just surfaces
    the "v1 → v2" transition on the terminal so the user isn't
    confused when a new file appears beside their original session.
    """
    candidates = discover_versions(path)
    if not candidates:
        return
    best_version, best_path = candidates[0]
    if best_version >= MAX_SUPPORTED_VERSION:
        return
    target = path_for_version(best_path, MAX_SUPPORTED_VERSION)
    logger.info(
        "Upgrading session format",
        source=str(best_path),
        source_version=best_version,
        target=str(target),
        target_version=MAX_SUPPORTED_VERSION,
    )
    print(
        f"[session.migration] upgrading {best_path.name} -> {target.name}",
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
    _announce_migration_if_needed(path)

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
