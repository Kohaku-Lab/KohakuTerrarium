"""CLI run command — launch a creature or recipe via the Terrarium engine.

A solo creature is added as a 1-creature graph with ``is_privileged=True``
so the user-facing creature has the full ``group_*`` tool surface. A
recipe is applied via :meth:`Terrarium.apply_recipe` and the privileged
root (declared via the recipe's ``root:``) hosts the user's TUI focus.

Both paths use the Terrarium engine. The full-screen engine TUI is the
default graph-aware surface; ``--mode cli`` mounts the rich inline CLI
for a focused single-creature stream.
"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.cli.picker import pick_runnable
from kohakuterrarium.packages.resolve import resolve_any_path
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.identity import drive_settings as _drive_settings
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.engine_cli import run_engine_with_tui
from kohakuterrarium.terrarium.engine_rich_cli import run_engine_with_rich_cli
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    get_logger,
    set_level,
)
from kohakuterrarium.utils.startup_trace import mark as mark_startup

logger = get_logger(__name__)

_SESSION_DIR = Path.home() / ".kohakuterrarium" / "sessions"


def _session_dir() -> Path:
    """Resolve the CLI session root using the shared configuration rules."""
    explicit = os.environ.get("KT_SESSION_DIR")
    if explicit:
        return Path(explicit).expanduser()
    docs_default = Path.home() / ".kohakuterrarium" / "sessions"
    if _SESSION_DIR != docs_default:
        return _SESSION_DIR
    return config_dir() / "sessions"


def run_agent_cli(
    agent_path: str,
    log_level: str,
    session: str | None = None,
    io_mode: str | None = None,
    llm: str | None = None,
    log_stderr: str = "auto",
    extra_creatures: list[str] | None = None,
    extra_channels: list[str] | None = None,
) -> int:
    """Run a creature or recipe from the CLI through the engine.

    ``io_mode`` is an *override* — when given, it replaces the focus
    creature's configured input/output modules with a user-facing
    shell:

    - ``"tui"``: Textual full-screen TUI with one tab per creature
      and one ``#channel`` tab per shared channel.
    - ``"cli"``: prompt-toolkit inline rich CLI. Single creature
      focus, output streams to scrollback.
    - ``"plain"``: not yet ported back from the pre-engine path.
    - ``"none"``: explicit "don't override anything" — same as
      omitting ``--mode``.

    When ``io_mode`` is omitted, the creature's configured IO
    modules drive the run. This is the path long-running headless
    background agents (Discord bot, webhook listener, custom polling
    input, etc.) depend on — overriding their IO with a TUI breaks
    them. Don't pass ``--mode`` and the engine just lets them run.

    ``extra_creatures`` and ``extra_channels`` let callers compose an
    ad-hoc team on the command line:

        kt run general --add critic --add planner --channel reviews

    Each ``--add`` spawns a non-privileged creature into the same
    graph; each ``--channel`` declares a shared channel and wires
    every creature as both listener and sender.
    """
    configure_utf8_stdio(log=True)
    set_level(log_level)
    # Stderr logging would corrupt prompt-toolkit's redraw region —
    # only enable it when the chosen surface leaves the terminal free.
    overlay_owns_terminal = io_mode in ("tui", "cli")
    if log_stderr == "on" or (log_stderr == "auto" and not overlay_owns_terminal):
        enable_stderr_logging(log_level)

    if io_mode == "plain":
        print(
            "Warning: --mode plain is not yet ported to the engine path; "
            "falling back to the creature's configured IO."
        )
        io_mode = None

    try:
        path = resolve_any_path(agent_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    if not path.exists():
        print(f"Error: path not found: {agent_path}")
        return 1

    try:
        return asyncio.run(
            _run(
                str(path),
                session=session,
                llm=llm,
                io_mode=io_mode,
                extra_creatures=extra_creatures or [],
                extra_channels=extra_channels or [],
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        logger.warning("kt run failed", error=str(exc), exc_info=True)
        return 1


def resolve_then_run(
    agent_path: str | None,
    *,
    io_mode: str,
    log_level: str = "INFO",
    session: str | None = "__auto__",
    llm: str | None = None,
    log_stderr: str = "auto",
    extra_creatures: list[str] | None = None,
    extra_channels: list[str] | None = None,
) -> int:
    """Resolve a creature/recipe (or run the startup picker) then run it.

    The shared core behind the ``kt-cli`` / ``kt-tui`` front doors and the
    ``kt cli`` / ``kt tui`` subcommand aliases.  ``io_mode`` is forced by
    the caller to ``"cli"`` or ``"tui"``.  When ``agent_path`` is ``None``
    the startup picker runs — which requires an interactive terminal; in a
    non-TTY context we print guidance and return non-zero instead of
    hanging on a picker nobody can drive.
    """
    if agent_path is None:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(
                "kt-cli / kt-tui need an interactive terminal to pick an agent.\n"
                "Pass a creature or recipe explicitly "
                "(e.g. `kt-cli @kt-biome/creatures/general`),\n"
                "or run a configured creature headless with `kt run <path>`."
            )
            return 2
        agent_path = pick_runnable(io_mode)
        if not agent_path:
            return 0
    return run_agent_cli(
        agent_path,
        log_level,
        session=session,
        io_mode=io_mode,
        llm=llm,
        log_stderr=log_stderr,
        extra_creatures=extra_creatures or [],
        extra_channels=extra_channels or [],
    )


async def _run(
    agent_path: str,
    *,
    session: str | None,
    llm: str | None,
    io_mode: str | None,
    extra_creatures: list[str],
    extra_channels: list[str],
) -> int:
    pwd = str(Path.cwd())
    is_recipe = _looks_like_recipe(agent_path)

    # Resolve node-local Drive settings once; absent or disabled settings keep
    # the engine Drive-free.
    drive_kwargs = _drive_settings.resolve_drive_kwargs()
    surface = io_mode or "configured"
    mark_startup("engine_create_begin", surface=surface)
    async with Terrarium(pwd=pwd, **drive_kwargs) as engine:
        mark_startup("engine_entered", surface=surface)
        store: SessionStore | None = None
        focus_creature_id = ""

        if is_recipe:
            cfg = load_terrarium_config(agent_path)
            # Interactive runs defer missing model credentials so the user can
            # rebind the model from the active surface.
            graph = await engine.apply_recipe(cfg, pwd=pwd, llm=llm, strict=False)
            focus_creature_id = _pick_focus_creature(engine, graph.graph_id)
            graph_id = graph.graph_id
            if session is not None:
                store = await _attach_session_store(
                    engine,
                    graph_id=graph_id,
                    session=session,
                    config_path=agent_path,
                    config_type="terrarium",
                )
        else:
            creature = await engine.add_creature(
                agent_path,
                llm=llm,
                pwd=pwd,
                is_privileged=True,
                # Interactive runs allow model binding repair at runtime.
                strict=False,
                # Terminal-owning surfaces must replace CLIInput before startup;
                # otherwise both consumers race for stdin. Configured I/O modes
                # require the creature to start immediately.
                start=(io_mode not in ("cli", "tui")),
            )
            focus_creature_id = creature.creature_id
            graph_id = creature.graph_id
            mark_startup(
                "creature_added",
                surface=surface,
                creature_id=focus_creature_id,
                graph_id=graph_id,
            )
            if session is not None:
                store = await _attach_session_store(
                    engine,
                    graph_id=graph_id,
                    session=session,
                    config_path=agent_path,
                    config_type="agent",
                )

        await _apply_cli_topology(
            engine,
            graph_id=graph_id,
            pwd=pwd,
            llm=llm,
            extra_creatures=extra_creatures,
            extra_channels=extra_channels,
        )

        try:
            if io_mode == "cli":
                mark_startup(
                    "surface_run_begin",
                    surface="cli",
                    creature_id=focus_creature_id,
                )
                await run_engine_with_rich_cli(engine, focus_creature_id, store)
            elif io_mode == "tui":
                mark_startup(
                    "surface_run_begin",
                    surface="tui",
                    creature_id=focus_creature_id,
                )
                await run_engine_with_tui(engine, focus_creature_id, store)
            else:
                # Configured I/O owns the lifecycle; keep the event loop alive
                # until the creature stops or the process is interrupted.
                creature = engine.get_creature(focus_creature_id)
                logger.info(
                    "kt run — creature using configured IO",
                    creature_id=focus_creature_id,
                    creature_name=creature.name,
                )
                while creature.is_running:
                    await asyncio.sleep(1)
        finally:
            if store is not None:
                if session is not None:
                    print(f"\nSession saved. To resume:")
                    print(f"  kt resume {Path(store.path).stem}")
                store.close()
        return 0


async def _apply_cli_topology(
    engine: Terrarium,
    *,
    graph_id: str,
    pwd: str,
    llm: str | None,
    extra_creatures: list[str],
    extra_channels: list[str],
) -> None:
    """Compose the on-the-fly team described by ``--add`` / ``--channel``.

    Spawned creatures join the existing ``graph_id`` so they share the
    session store and channel registry. Channels are declared
    graph-wide; every creature in the graph (including the original)
    is wired as both listener and sender, matching what the user
    typically wants when assembling an ad-hoc review/peer setup from
    the command line.
    """
    if not extra_creatures and not extra_channels:
        return
    for cfg_path in extra_creatures:
        try:
            await engine.add_creature(
                cfg_path,
                graph=graph_id,
                pwd=pwd,
                llm=llm,
                is_privileged=False,
                strict=False,
            )
        except Exception as exc:
            logger.warning(
                "kt run --add failed", config=cfg_path, error=str(exc), exc_info=True
            )
            print(f"Warning: --add {cfg_path} failed: {exc}")
    for ch_name in extra_channels:
        try:
            await engine.add_channel(graph_id, ch_name)
        except Exception as exc:
            logger.warning(
                "kt run --channel failed",
                channel=ch_name,
                error=str(exc),
                exc_info=True,
            )
            print(f"Warning: --channel {ch_name} failed: {exc}")
            continue
        graph = engine.get_graph(graph_id)
        for cid in sorted(graph.creature_ids):
            try:
                _topo.set_listen(engine._topology, cid, ch_name, listening=True)
                _topo.set_send(engine._topology, cid, ch_name, sending=True)
                creature = engine.get_creature(cid)
                env = engine._environments.get(graph_id)
                if env is None:
                    continue
                _channels.inject_channel_trigger(
                    creature.agent,
                    subscriber_id=creature.name,
                    channel_name=ch_name,
                    registry=env.shared_channels,
                    ignore_sender=creature.name,
                    ignore_sender_id=creature.creature_id,
                )
                if ch_name not in creature.listen_channels:
                    creature.listen_channels.append(ch_name)
                if ch_name not in creature.send_channels:
                    creature.send_channels.append(ch_name)
            except Exception as exc:
                logger.warning(
                    "wire on --channel failed",
                    channel=ch_name,
                    creature=cid,
                    error=str(exc),
                    exc_info=True,
                )


def _looks_like_recipe(path: str) -> bool:
    """Return whether a path appears to describe a terrarium recipe."""
    p = Path(path)
    candidates = (
        p / "terrarium.yaml",
        p / "terrarium.yml",
        p / "recipe.yaml",
    )
    if any(c.exists() for c in candidates):
        return True
    if p.is_file() and p.suffix.lower() in (".yaml", ".yml"):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            return False
        return "creatures:" in text and ("channels:" in text or "root:" in text)
    return False


def _pick_focus_creature(engine: Terrarium, graph_id: str) -> str:
    """Return the creature_id the TUI should focus on.

    Preference order: the privileged root (recipe-declared), the first
    privileged creature, the first creature in the graph.
    """
    graph = engine.get_graph(graph_id)
    privileged: list[str] = []
    fallback: list[str] = []
    for cid in sorted(graph.creature_ids):
        try:
            c = engine.get_creature(cid)
        except KeyError:
            continue
        if getattr(c, "is_privileged", False):
            privileged.append(cid)
        else:
            fallback.append(cid)
    if privileged:
        return privileged[0]
    if fallback:
        return fallback[0]
    raise RuntimeError(f"graph {graph_id!r} has no creatures to focus on")


async def _attach_session_store(
    engine: Terrarium,
    *,
    graph_id: str,
    session: str,
    config_path: str,
    config_type: str,
) -> SessionStore:
    """Attach a session store to ``graph_id`` and return it.

    ``config_type`` is written after attachment because graph shape alone
    cannot distinguish a one-creature recipe from a solo creature, while resume
    still needs the recipe type to rebuild topology.
    """
    if session == "__auto__":
        session_root = _session_dir()
        session_root.mkdir(parents=True, exist_ok=True)
        session_file = session_root / f"{graph_id}_{uuid4().hex[:8]}.kohakutr"
    else:
        session_file = Path(session)

    await engine.attach_session(graph_id, session_file)
    store = engine._session_stores[graph_id]
    if config_path and not store.meta.get("config_path"):
        store.meta["config_path"] = config_path
    if config_type in ("agent", "terrarium"):
        store.meta["config_type"] = config_type
    return store


def _resolve_session(query: str | None, last: bool = False) -> Path | None:
    """Resolve a session query to a file path. Used by ``kt resume``.

    Searches the configured session directory for matching files.
    Accepts: full path, filename, name prefix, or None (list/pick).
    """
    if query and Path(query).exists():
        return Path(query)

    if query:
        for ext in (".kohakutr", ".kt"):
            if query.endswith(ext):
                query = query[: -len(ext)]
                break

    session_root = _session_dir()
    if not session_root.exists():
        return None

    sessions = sorted(
        [*session_root.glob("*.kohakutr"), *session_root.glob("*.kt")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not sessions:
        return None

    if last:
        return sessions[0]

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

    p = Path(query)
    if p.exists():
        return p
    for ext in (".kohakutr", ".kt"):
        if (session_root / f"{query}{ext}").exists():
            return session_root / f"{query}{ext}"

    return None


def _session_preview(path: Path) -> str:
    """Get a short preview of session metadata."""
    store = None
    try:
        # Read-only: a plain open+close here used to bump last_active,
        # corrupting the recency ordering the resume picker sorts by.
        store = SessionStore.open_readonly(path)
        meta = store.load_meta()
        config_type = meta.get("config_type", "?")
        config_path = meta.get("config_path", "")
        name = Path(config_path).name if config_path else "?"
        return f"({config_type}: {name})"
    except Exception as e:
        logger.warning("Failed to read session label", error=str(e), exc_info=True)
        return ""
    finally:
        if store is not None:
            try:
                store.close()
            except Exception as e:
                logger.warning(
                    "Failed to close session preview store",
                    error=str(e),
                    exc_info=True,
                )
