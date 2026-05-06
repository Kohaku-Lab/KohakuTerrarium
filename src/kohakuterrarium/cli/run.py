"""CLI run command — launch a creature or recipe via the Terrarium engine.

A solo creature is added as a 1-creature graph with ``is_privileged=True``
so the user-facing creature has the full ``group_*`` tool surface. A
recipe is applied via :meth:`Terrarium.apply_recipe` and the privileged
root (declared via the recipe's ``root:``) hosts the user's TUI focus.

Both paths use the engine TUI from :mod:`terrarium.engine_cli`. The
Rich-CLI variant has been removed in favor of the engine TUI.
"""

import asyncio
from pathlib import Path
from uuid import uuid4

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.engine_cli import run_engine_with_tui
from kohakuterrarium.utils.logging import (
    configure_utf8_stdio,
    enable_stderr_logging,
    get_logger,
    set_level,
)

logger = get_logger(__name__)

_SESSION_DIR = Path.home() / ".kohakuterrarium" / "sessions"


def run_agent_cli(
    agent_path: str,
    log_level: str,
    session: str | None = None,
    io_mode: str | None = None,
    llm_override: str | None = None,
    log_stderr: str = "auto",
) -> int:
    """Run a creature or recipe from the CLI through the engine.

    ``io_mode`` is currently accepted but ignored — every ``kt run``
    invocation uses the engine TUI. ``cli`` / ``plain`` variants will
    return in a follow-up; until then we warn when the user asks for
    one explicitly so the silence isn't surprising.
    """
    configure_utf8_stdio(log=True)
    set_level(log_level)
    if log_stderr in ("on", "auto"):
        enable_stderr_logging(log_level)

    if io_mode in ("cli", "plain"):
        print(
            f"Warning: --mode {io_mode} is not yet supported on the engine "
            "path; using the TUI instead."
        )

    path = Path(agent_path)
    if not path.exists():
        print(f"Error: path not found: {agent_path}")
        return 1

    try:
        return asyncio.run(
            _run(
                str(path),
                session=session,
                llm_override=llm_override,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        logger.debug("kt run failed", error=str(exc), exc_info=True)
        return 1


async def _run(
    agent_path: str,
    *,
    session: str | None,
    llm_override: str | None,
) -> int:
    pwd = str(Path.cwd())
    is_recipe = _looks_like_recipe(agent_path)

    async with Terrarium(pwd=pwd) as engine:
        store: SessionStore | None = None
        focus_creature_id = ""

        if is_recipe:
            cfg = load_terrarium_config(agent_path)
            graph = await engine.apply_recipe(cfg, pwd=pwd, llm_override=llm_override)
            focus_creature_id = _pick_focus_creature(engine, graph.graph_id)
            if session is not None:
                store = await _attach_session_store(
                    engine,
                    graph_id=graph.graph_id,
                    session=session,
                    config_path=agent_path,
                    config_type="terrarium",
                )
        else:
            creature = await engine.add_creature(
                agent_path,
                llm_override=llm_override,
                pwd=pwd,
                is_privileged=True,
            )
            focus_creature_id = creature.creature_id
            if session is not None:
                store = await _attach_session_store(
                    engine,
                    graph_id=creature.graph_id,
                    session=session,
                    config_path=agent_path,
                    config_type="agent",
                )

        try:
            await run_engine_with_tui(engine, focus_creature_id, store)
        finally:
            if store is not None:
                if session is not None:
                    print(f"\nSession saved. To resume:")
                    print(f"  kt resume {Path(store.path).stem}")
                store.close()
        return 0


def _looks_like_recipe(path: str) -> bool:
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

    Awaits the engine's :meth:`attach_session` so a failure surfaces
    here (rather than disappearing into a fire-and-forget task).
    """
    if session == "__auto__":
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        session_file = _SESSION_DIR / f"{graph_id}_{uuid4().hex[:8]}.kohakutr"
    else:
        session_file = Path(session)

    store = SessionStore(session_file)
    store.init_meta(
        session_id=uuid4().hex,
        config_type=config_type,
        config_path=config_path,
        pwd=str(Path.cwd()),
        agents=[c.name for c in engine.list_creatures() if c.graph_id == graph_id],
    )
    await engine.attach_session(graph_id, store)
    return store


def _resolve_session(query: str | None, last: bool = False) -> Path | None:
    """Resolve a session query to a file path. Used by ``kt resume``.

    Searches ~/.kohakuterrarium/sessions/ for matching files.
    Accepts: full path, filename, name prefix, or None (list/pick).
    """
    if query and Path(query).exists():
        return Path(query)

    if query:
        for ext in (".kohakutr", ".kt"):
            if query.endswith(ext):
                query = query[: -len(ext)]
                break

    if not _SESSION_DIR.exists():
        return None

    sessions = sorted(
        _SESSION_DIR.glob("*.kohakutr"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    sessions.extend(
        sorted(
            _SESSION_DIR.glob("*.kt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
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
