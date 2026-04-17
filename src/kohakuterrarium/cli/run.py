"""CLI run command — launch an agent from a config folder."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from kohakuterrarium.builtins.cli_rich.app import RichCLIApp
from kohakuterrarium.builtins.cli_rich.input import RichCLIInput
from kohakuterrarium.builtins.cli_rich.output import RichCLIOutput
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.session.resume import _create_io_modules
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger, set_level

logger = get_logger(__name__)

_SESSION_DIR = Path.home() / ".kohakuterrarium" / "sessions"


async def _run_agent_rich_cli(agent: Agent) -> None:
    """Run an agent under the rich CLI (prompt_toolkit Application).

    The main loop is driven by RichCLIApp, not agent.run(). We start
    the agent's modules manually, replay any pending resume events into
    real terminal scrollback, then enter the rich CLI loop.
    """
    app = RichCLIApp(agent)
    # Replace the agent's default output with one wired to the rich CLI app
    rich_output = RichCLIOutput(app)
    agent.output_router.default_output = rich_output

    await agent.start()

    # Resume: replay session history to scrollback. Normally agent.run()
    # would do this, but we own the main loop so we have to do it
    # ourselves. Has to happen BEFORE app.run_async() so the writes go
    # to real terminal scrollback (not into prompt_toolkit's screen).
    pending = getattr(agent, "_pending_resume_events", None)
    if pending:
        try:
            app.replay_session(pending)
        except Exception as e:
            logger.debug("Failed to replay session history", error=str(e))
        agent._pending_resume_events = None

    try:
        await app.run()
    finally:
        await agent.stop()


def _has_custom_io(config) -> tuple[bool, bool]:
    """Return whether the creature config uses custom/package input/output."""
    custom_input = config.input.type in {"custom", "package"}
    custom_output = config.output.type in {"custom", "package"}
    return custom_input, custom_output


def _warn_io_override_if_needed(config, io_mode: str) -> None:
    """Warn when an explicit CLI mode overrides configured custom I/O."""
    custom_input, custom_output = _has_custom_io(config)
    overridden: list[str] = []
    if custom_input:
        overridden.append(f"input={config.input.type}")
    if custom_output:
        overridden.append(f"output={config.output.type}")
    if not overridden:
        return
    joined = ", ".join(overridden)
    print(
        "Warning: --mode "
        f"{io_mode} overrides configured custom I/O ({joined})."
    )


def run_agent_cli(
    agent_path: str,
    log_level: str,
    session: str | None = None,
    io_mode: str | None = None,
    llm_override: str | None = None,
) -> int:
    """Run an agent from CLI."""

    # Setup logging
    set_level(log_level)

    # Check path exists
    path = Path(agent_path)
    if not path.exists():
        print(f"Error: Agent path not found: {agent_path}")
        return 1

    config_file = path / "config.yaml"
    if not config_file.exists():
        config_file = path / "config.yml"
        if not config_file.exists():
            print(f"Error: No config.yaml found in {agent_path}")
            return 1

    config = load_agent_config(str(path))

    # If the user does not specify --mode, respect the creature's configured
    # input/output modules exactly. Only explicit --mode should override them.
    resolved_mode = io_mode
    use_rich_cli = resolved_mode == "cli"

    store = None
    session_file = None
    try:
        io_kwargs: dict = {}
        if resolved_mode is not None:
            _warn_io_override_if_needed(config, resolved_mode)
            if use_rich_cli:
                io_kwargs["input_module"] = RichCLIInput()
                io_kwargs["output_module"] = RichCLIOutput(app=None)
            else:
                inp, out = _create_io_modules(resolved_mode)
                io_kwargs["input_module"] = inp
                io_kwargs["output_module"] = out

        # Create agent
        agent = Agent.from_path(str(path), llm_override=llm_override, **io_kwargs)

        # Attach session store (default: ON)
        if session is not None:
            if session == "__auto__":
                _SESSION_DIR.mkdir(parents=True, exist_ok=True)
                session_file = (
                    _SESSION_DIR / f"{agent.config.name}_{id(agent):08x}.kohakutr"
                )
            else:
                session_file = Path(session)

            store = SessionStore(session_file)
            store.init_meta(
                session_id=uuid4().hex,
                config_type="agent",
                config_path=str(path),
                pwd=str(Path.cwd()),
                agents=[agent.config.name],
            )
            agent.attach_session_store(store)

        if use_rich_cli:
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
        if session_file and session_file.exists():
            print(f"\nSession saved. To resume:")
            print(f"  kt resume {session_file.stem}")


def _resolve_session(query: str | None, last: bool = False) -> Path | None:
    """Resolve a session query to a file path.

    Searches ~/.kohakuterrarium/sessions/ for matching files.
    Accepts: full path, filename, name prefix, or None (list/pick).
    """
    # Full path provided
    if query and Path(query).exists():
        return Path(query)

    # Strip extension from query if present (user may paste from hint)
    if query:
        for ext in (".kohakutr", ".kt"):
            if query.endswith(ext):
                query = query[: -len(ext)]
                break

    # Search in default session directory
    if not _SESSION_DIR.exists():
        return None

    sessions = sorted(
        _SESSION_DIR.glob("*.kohakutr"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Also check legacy .kt files (pre-.kohakutr extension)
    sessions.extend(
        sorted(
            _SESSION_DIR.glob("*.kt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    )

    if not sessions:
        return None

    # --last: most recent
    if last:
        return sessions[0]

    # No query: list recent and let user pick
    if not query:
        print("Recent sessions:")
        shown = sessions[:10]
        for i, s in enumerate(shown, 1):
            meta = _session_preview(s)
            print(f"  {i}. {s.name}  {meta}")
        print()
        try:
            choice = input(f"Pick [1-{len(shown)}] or name prefix: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(shown):
                return shown[idx]
            return None
        query = choice

    # Prefix match
    matches = [s for s in sessions if s.stem.startswith(query) or query in s.stem]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Multiple matches for '{query}':")
        for i, s in enumerate(matches[:10], 1):
            meta = _session_preview(s)
            print(f"  {i}. {s.name}  {meta}")
        print()
        try:
            choice = input(f"Pick [1-{len(matches[:10])}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(matches[:10]):
                return matches[idx]
        return None

    # No match in session dir, try as path
    p = Path(query)
    if p.exists():
        return p
    # Try appending extension
    for ext in (".kohakutr", ".kt"):
        if (_SESSION_DIR / f"{query}{ext}").exists():
            return _SESSION_DIR / f"{query}{ext}"

    return None


def _session_preview(path: Path) -> str:
    """Get a short preview of session metadata."""
    try:
        store = SessionStore(path)
        meta = store.load_meta()
        store.close()
        config_type = meta.get("config_type", "?")
        config_path = meta.get("config_path", "")
        name = Path(config_path).name if config_path else "?"
        return f"({config_type}: {name})"
    except Exception as e:
        logger.debug("Failed to read session label", error=str(e))
        return ""
