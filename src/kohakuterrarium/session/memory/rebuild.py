"""Explicit offline maintenance of derived Session Memory indexes."""

from __future__ import annotations

import re
import sqlite3
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from kohakuvault import VectorKVault

from kohakuterrarium.session.memory.blocks import extract_batch
from kohakuterrarium.session.memory.embedding import (
    BaseEmbedder,
    NullEmbedder,
    create_embedder,
)
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.source_events import sourced_events
from kohakuterrarium.session.occupancy.gate import resource_gate
from kohakuterrarium.session.occupancy.registry import live_store_for
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store.lifecycle.lock import (
    acquire_writer_lock,
    release_writer_lock,
    unique_lock_authorities,
)
from kohakuterrarium.session.store.lifecycle.version import require_writable_format
from kohakuterrarium.session.vault.read import is_snapshot_target
from kohakuterrarium.utils.file.path import coerce_fs_path

_VECTOR_TABLE = re.compile(r"memory_vec_(\d+)d\Z")


class RebuildRefused(ValueError):
    """A rebuild needs an explicit choice or a safe, stopped Session."""


@dataclass(frozen=True)
class IndexInventory:
    fts_rows: int
    vector_rows: tuple[tuple[int, int], ...]
    database_bytes: int

    @property
    def vectors_present(self) -> bool:
        return any(rows for _, rows in self.vector_rows)


@dataclass(frozen=True)
class RebuildResult:
    agents: tuple[str, ...]
    events: int
    fts_rows: int
    vector_rows: int
    vector_policy: str
    backup: Path


@contextmanager
def _offline_database(path: Path):
    """Read inventory or make a backup with no concurrent native handles."""
    if live_store_for(path) is not None:
        raise RebuildRefused("Session is currently open; close it before rebuilding.")
    admission = resource_gate.try_fast(path, eligible=True)
    if admission.lease is None:
        raise RebuildRefused(
            f"Session has active or uncertain native occupancy ({admission.reason}); "
            "close Store/Memory handles before rebuilding."
        )
    db = None
    released = False
    try:
        source = path.resolve(strict=True)
        mode = "immutable=1" if is_snapshot_target(source, True) else "mode=ro"
        db = sqlite3.connect(source.as_uri() + "?" + mode, uri=True, timeout=0)
        yield db
    finally:
        try:
            if db is not None:
                db.close()
            released = True
        finally:
            admission.lease.finish(source_released=released)


def _inventory(db, size: int) -> IndexInventory:
    names = {name for (name,) in db.execute("SELECT name FROM sqlite_master")}
    fts_rows = (
        int(db.execute("SELECT COUNT(*) FROM memory_fts_values").fetchone()[0])
        if "memory_fts_values" in names
        else 0
    )
    vectors = []
    for name in sorted(names):
        found = _VECTOR_TABLE.fullmatch(name)
        if not found:
            continue
        shadow = name + "_rowids"
        if shadow not in names:
            raise RebuildRefused(f"Cannot account for vector table {name!r}.")
        count = int(db.execute(f'SELECT COUNT(*) FROM "{shadow}"').fetchone()[0])
        vectors.append((int(found.group(1)), count))
    return IndexInventory(fts_rows, tuple(vectors), size)


def inspect_index(path: Path) -> IndexInventory:
    """Inspect existing index counts without opening writable Vaults."""
    source = coerce_fs_path(path)
    with _offline_database(source) as db:
        return _inventory(db, source.stat().st_size)


def _backup(db, source: Path) -> Path:
    """Create and validate a logical SQLite backup, including committed WAL data."""
    path = source.with_name(
        f"{source.name}.before-memory-rebuild-{uuid4().hex[:12]}.bak"
    )
    try:
        with closing(sqlite3.connect(path)) as writer:
            db.backup(writer)
            if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RebuildRefused("Backup verification failed.")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


@contextmanager
def _vector_table(memory: SessionMemory, dims: int):
    if memory._vec is not None and memory._active_vector_dims == dims:
        yield memory._vec
        return
    vault = memory._resources.open(
        VectorKVault, memory._path, table=f"memory_vec_{dims}d", dimensions=dims
    )
    try:
        yield vault
    finally:
        memory._resources.drop(vault)


def _source_agents(
    store, memory: SessionMemory, dims: tuple[int, ...]
) -> tuple[str, ...]:
    names = {
        name
        for name in store.load_meta().get("agents", ())
        if isinstance(name, str) and name
    }
    physical = set()
    for key in store._event_key_names():
        prefix, sep, suffix = key.rpartition(":e")
        if sep and prefix and suffix.isascii() and suffix.isdecimal():
            physical.add(prefix)

    def require_source(rows):
        for _, meta in rows:
            if (
                isinstance(meta, dict)
                and meta.get("agent")
                and meta["agent"] not in physical
            ):
                raise RebuildRefused(
                    f"Indexed Agent {meta['agent']!r} has no physical source events; "
                    "refusing to replace its Search index."
                )

    require_source(memory._index_rows("keywords"))
    for dim in dims:
        with _vector_table(memory, dim) as vault:
            require_source(memory._index_rows(f"vectors:{dim}", vault))
    return tuple(sorted(names | physical))


def _prepare_events(store, names):
    events = {}
    for name in names:
        try:
            rows = sourced_events(store, name, strict=True)
            extract_batch(name, rows)
        except Exception as exc:
            raise RebuildRefused(
                f"Cannot read or project source events for {name!r}: {exc}"
            ) from exc
        events[name] = rows
    return events


def _resolve_embedder(store, provider, model, dimensions, embedder):
    if embedder is not None:
        return embedder, None
    saved = store.state.get("embedding_config")
    config = dict(saved) if isinstance(saved, dict) else {}
    for key, value in (
        ("provider", provider),
        ("model", model),
        ("dimensions", dimensions),
    ):
        if value is not None:
            config[key] = value
    if not config.get("provider") or config["provider"] == "none":
        raise RebuildRefused("Provide an embedding provider for vector rebuild.")
    return create_embedder(config), config


def _rebuild_indexes(store, memory, events, dims, policy, embedder, config):
    if policy == "rebuild":
        memory._set_embedder(embedder)
        if config:
            store.state["embedding_config"] = config
    elif policy == "drop":
        store.state["embedding_config"] = {"provider": "none"}

    active = memory._active_vector_dims if policy == "rebuild" else 0
    for name in events:
        memory._set_indexed_count(name, 0)
        memory._state[f"{name}:cursor:keywords:pending"] = True
        for dim in set(dims) | ({active} if active else set()):
            memory._state[f"{name}:cursor:vectors:{dim}:pending"] = True

    memory._clear_index(None, "keywords")
    for name, rows in events.items():
        memory._index_keywords(name, rows)

    if active:
        # Remove legacy rows without a known namespace, then rebuild using
        # the ordinary pending/cursor protocol. Encoding precedes clearing.
        kind = f"vectors:{active}"
        for row, meta in memory._index_rows(kind):
            if not isinstance(meta, dict) or meta.get("agent") not in events:
                memory._vec.delete(row)
        for name, rows in events.items():
            batch, blocks = memory._vector_batch(name, rows)
            vectors = embedder.encode([b.content for b in blocks]) if blocks else []
            memory._commit_vectors(name, batch, len(rows), blocks, vectors)

    for dim in dims:
        if dim != active:
            with _vector_table(memory, dim) as vault:
                memory._clear_index(None, f"vectors:{dim}", vault)

    if not active:
        memory._state.delete("vec_dimensions")
    memory._state.delete("memory:index_manifest")
    return memory._fts.count(), memory._vec.count() if active else 0


def rebuild_session_memory(
    path: str | Path,
    *,
    vectors: str = "auto",
    provider: str | None = None,
    model: str | None = None,
    dimensions: int | None = None,
    embedder: BaseEmbedder | None = None,
) -> RebuildResult:
    """Rebuild offline through the original Store; incomplete indexes can be retried."""
    source = coerce_fs_path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if vectors not in {"auto", "drop", "rebuild"}:
        raise ValueError("vectors must be auto, drop, or rebuild")
    if vectors != "rebuild" and any(
        item is not None for item in (provider, model, dimensions, embedder)
    ):
        raise ValueError("Embedding settings require --vectors rebuild")

    actual = source.resolve(strict=True)
    guards = ExitStack()
    backup = None
    try:
        for authority in unique_lock_authorities([source, actual], ".lock"):
            guards.callback(release_writer_lock, acquire_writer_lock(authority))
        # The same admission used by Store opens, before creating a backup.
        require_writable_format(source)
        with _offline_database(source) as db:
            inventory = _inventory(db, source.stat().st_size)
            if vectors == "auto" and inventory.vectors_present:
                raise RebuildRefused(
                    "Existing vectors require --vectors rebuild (may incur model/API "
                    "cost) or --vectors drop (disable semantic search)."
                )
            backup = _backup(db, source)

        policy = "fts" if vectors == "auto" else vectors
        dims = tuple(dim for dim, _ in inventory.vector_rows)
        with ExitStack() as stack:
            store = SessionStore(actual)
            stack.callback(store.close, update_status=False)
            memory = SessionMemory(store.path)
            stack.callback(memory.close)
            names = _source_agents(store, memory, dims)
            events = _prepare_events(store, names)
            config = None
            if policy == "rebuild":
                embedder, config = _resolve_embedder(
                    store, provider, model, dimensions, embedder
                )
                if isinstance(embedder, NullEmbedder) or embedder.dimensions <= 0:
                    raise RebuildRefused("Vector rebuild needs a real embedding model.")
            fts, vec = _rebuild_indexes(
                store, memory, events, dims, policy, embedder, config
            )
        return RebuildResult(
            names, sum(map(len, events.values())), fts, vec, policy, backup
        )
    except Exception as exc:
        if backup is None:
            raise
        raise RebuildRefused(f"{exc} (original database backup: {backup})") from exc
    finally:
        guards.close()
