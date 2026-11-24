"""Offline Session-wide Memory index rebuild with copy-then-replace publication.

The source event log is never rewritten in place. Index work occurs in a
temporary Session copy; the original database is backed up before atomic
replacement. All writers must be stopped before starting this operation.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from kohakuvault import TextVault, VectorKVault

from kohakuterrarium.session.memory.blocks import _anchor
from kohakuterrarium.session.memory.embedding import (
    BaseEmbedder,
    NullEmbedder,
    create_embedder,
)
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.source_events import SourcedEvent, sourced_events
from kohakuterrarium.session.occupancy.registry import live_store_for
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.store.lifecycle.lock import (
    acquire_writer_lock,
    release_writer_lock,
)
from kohakuterrarium.session.vault.io import key_text, scan_keys

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


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_uri(path: Path) -> str:
    # The caller rejects non-empty WAL before inspecting. Immutable read
    # prevents a read-only plan from creating -wal/-shm sidecars.
    return path.resolve().as_uri() + "?mode=ro&immutable=1"


def inspect_index(path: Path) -> IndexInventory:
    """Inspect only SQLite schema/row counts; do not create index handles."""
    source = Path(path).resolve(strict=True)
    _reject_busy_source(source)
    with closing(sqlite3.connect(_database_uri(source), uri=True)) as db:
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
            # vec0's row-id shadow is normal SQLite, accessible read-only
            # without loading the virtual-table extension.
            shadow = name + "_rowids"
            if shadow not in names:
                raise RebuildRefused(
                    f"Vector table {name!r} has no row-id shadow; "
                    "cannot safely discard unaccounted vectors."
                )
            count = int(db.execute(f'SELECT COUNT(*) FROM "{shadow}"').fetchone()[0])
            vectors.append((int(found.group(1)), count))
    return IndexInventory(fts_rows, tuple(vectors), source.stat().st_size)


def _reject_busy_source(path: Path) -> None:
    if live_store_for(path) is not None:
        raise RebuildRefused(
            "Session is currently open in this process; stop its agents."
        )
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise RebuildRefused(
            "Session has an uncheckpointed SQLite WAL; stop all writers and "
            "close/checkpoint the Session before rebuilding."
        )
    if Path(str(path) + "-shm").exists():
        raise RebuildRefused(
            "Session still has an SQLite shared-memory sidecar; close "
            "other readers and writers before rebuilding."
        )


def _source_digest(store, names: tuple[str, ...]) -> tuple:
    """Fingerprint exact physical events, not mutable index-ordinal positions."""
    digest = hashlib.sha256()
    total = 0
    for name in names:
        events = sourced_events(store, name)
        if events and not all(isinstance(evt, SourcedEvent) for evt in events):
            raise RebuildRefused(
                f"Cannot certify original physical source for {name!r}; "
                "indexing and Anchors must not guess event positions."
            )
        for event in events:
            digest.update(name.encode("utf-8"))
            digest.update(event.source_ref.encode("utf-8"))
            digest.update(_anchor(event).encode("ascii"))
            total += 1
    return total, digest.hexdigest()


def _source_agents(store, memory) -> tuple[str, ...]:
    """Rebuild all known indexed/registered event namespaces, not one Agent."""
    meta = store.load_meta()
    names = {x for x in meta.get("agents", ()) if isinstance(x, str) and x}
    # Search may index attached agents that are absent from the Session header.
    for key in scan_keys(store.events):
        text = key_text(key)
        prefix, sep, suffix = text.rpartition(":e")
        if sep and prefix and suffix.isascii() and suffix.isdecimal():
            names.add(prefix)
    for row_id in memory._fts.keys(limit=memory._fts.count()):
        _, row_meta = memory._fts.get_by_id(row_id)
        if isinstance(row_meta, dict) and row_meta.get("agent"):
            if row_meta["agent"] not in names:
                raise RebuildRefused(
                    f"Indexed Agent {row_meta['agent']!r} has no identifiable "
                    "source namespace; manual source repair is required."
                )
    return tuple(sorted(names))


def _clear_derived_indexes(memory, table_dims: tuple[int, ...], path: Path) -> None:
    """Remove all old projection metadata/tables on the *staging copy only*."""
    for row in memory._fts.keys(limit=memory._fts.count()):
        memory._fts.delete(row)
    for dims in table_dims:
        reader = memory._resources.open(
            TextVault, str(path), table=f"memory_vec_{dims}d"
        )
        try:
            # Some old tables have different dimensions or are not the active
            # vector table. Never rely on the one saved vec_dimensions key.
            ids = reader.keys(limit=reader.count())
        finally:
            memory._resources.drop(reader)
        if not ids:
            continue
        vault = (
            memory._vec
            if memory._vec is not None and memory._state.get("vec_dimensions") == dims
            else memory._resources.open(
                VectorKVault,
                str(path),
                table=f"memory_vec_{dims}d",
                dimensions=dims,
            )
        )
        try:
            for row in ids:
                vault.delete(row)
            if vault.count():
                raise RebuildRefused(
                    f"Staged vector table memory_vec_{dims}d was not fully cleared."
                )
        finally:
            if vault is not memory._vec:
                memory._resources.drop(vault)
    for key in list(memory._state.keys()):
        memory._state.delete(key)


def _rebuild_staging(
    stage: Path,
    *,
    policy: str,
    inventory: IndexInventory,
    embedder: BaseEmbedder | None,
    embed_config: dict | None,
) -> tuple[tuple[str, ...], int, int, int]:
    store = SessionStore(stage)
    try:
        memory = SessionMemory(str(stage))
        try:
            names = _source_agents(store, memory)
            baseline = _source_digest(store, names)
            _clear_derived_indexes(
                memory, tuple(dims for dims, _ in inventory.vector_rows), stage
            )
        finally:
            memory.close()

        if policy == "drop":
            # A stale model config must not silently re-embed on next live
            # semantic request after explicit FTS-only reconstruction.
            store.state["embedding_config"] = {"provider": "none"}
        elif policy == "rebuild" and embed_config:
            store.state["embedding_config"] = dict(embed_config)

        rebuilt = SessionMemory(str(stage), embedder if policy == "rebuild" else None)
        try:
            for name in names:
                events = sourced_events(store, name)
                if events and not all(isinstance(evt, SourcedEvent) for evt in events):
                    raise RebuildRefused(
                        f"Unverifiable physical event source for {name!r}"
                    )
                rebuilt.index_events(name, events)
            fts_count = rebuilt._fts.count()
            vector_count = (
                rebuilt._vec.count() if policy == "rebuild" and rebuilt._vec else 0
            )
            for row in rebuilt._fts.keys(limit=fts_count):
                _, meta = rebuilt._fts.get_by_id(row)
                if not meta.get("source_ref") or not meta.get("source_fingerprint"):
                    raise RebuildRefused(
                        "Rebuilt FTS row is missing physical provenance."
                    )
            if policy == "rebuild":
                if rebuilt._vec is None or vector_count != fts_count:
                    raise RebuildRefused("Rebuilt Vector and FTS block sets differ.")
                dims = embedder.dimensions
                reader = rebuilt._resources.open(
                    TextVault, str(stage), table=f"memory_vec_{dims}d"
                )
                try:
                    vector_ids = reader.keys(limit=reader.count())
                finally:
                    rebuilt._resources.drop(reader)
                for row in vector_ids:
                    _, meta = rebuilt._vec.get_by_id(row)
                    if not meta.get("source_ref") or not meta.get("source_fingerprint"):
                        raise RebuildRefused(
                            "Rebuilt Vector row is missing physical provenance."
                        )
        finally:
            rebuilt.close()
        store.checkpoint()
        if _source_digest(store, names) != baseline:
            raise RebuildRefused(
                "Source events changed in staged copy; publication aborted."
            )
        return names, baseline[0], fts_count, vector_count
    finally:
        store.close(update_status=False)


def rebuild_session_memory(
    path: str | Path,
    *,
    vectors: str = "auto",
    provider: str | None = None,
    model: str | None = None,
    dimensions: int | None = None,
    embedder: BaseEmbedder | None = None,
) -> RebuildResult:
    """Stage complete index rebuild; publish only after source verification.

    Existing vector data requires an explicit rebuild/drop decision. 'auto'
    applies to FTS-only Sessions without stored vectors.
    """
    source = Path(path).resolve(strict=True)
    if vectors not in {"auto", "drop", "rebuild"}:
        raise ValueError("vectors must be auto, drop, or rebuild")
    if vectors != "rebuild" and any(
        item is not None for item in (provider, model, dimensions, embedder)
    ):
        raise ValueError("Embedding settings require --vectors rebuild")
    _reject_busy_source(source)
    lock = acquire_writer_lock(str(source))
    stage = source.with_name(f"{source.stem}.memory-rebuild-{uuid4().hex}.kohakutr")
    try:
        _reject_busy_source(source)
        before = source.stat()
        before_digest = _file_digest(source)
        inventory = inspect_index(source)
        if vectors == "auto" and inventory.vectors_present:
            details = ", ".join(
                f"{rows}x{dim}d" for dim, rows in inventory.vector_rows if rows
            )
            raise RebuildRefused(
                f"Session contains {details} old vector rows. Choose "
                "'--vectors rebuild' (model/API cost) or '--vectors drop' "
                "(disable semantic index, original retained in backup)."
            )
        policy = "fts" if vectors == "auto" else vectors
        config = None
        if policy == "rebuild":
            if embedder is None:
                store = SessionStore.open_readonly(source)
                try:
                    saved = store.state.get("embedding_config")
                finally:
                    store.close(update_status=False)
                config = dict(saved) if isinstance(saved, dict) else {}
                if provider is not None:
                    config["provider"] = provider
                if model is not None:
                    config["model"] = model
                if dimensions is not None:
                    config["dimensions"] = dimensions
                if not config.get("provider") or config["provider"] == "none":
                    raise RebuildRefused(
                        "No usable saved embedding provider; provide --provider "
                        "and optionally --model/--dimensions for vector rebuild."
                    )
                embedder = create_embedder(config)
            if isinstance(embedder, NullEmbedder) or embedder.dimensions <= 0:
                raise RebuildRefused("Vector rebuild needs a real embedding model.")
        shutil.copy2(source, stage)
        names, events, fts, vec = _rebuild_staging(
            stage,
            policy=policy,
            inventory=inventory,
            embedder=embedder,
            embed_config=config,
        )
        for suffix in ("-wal", "-shm"):
            if Path(str(stage) + suffix).exists():
                raise RebuildRefused(
                    f"Staged SQLite {suffix} exists after clean close; "
                    "cannot publish an incomplete file snapshot."
                )
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RebuildRefused(
                "Source database changed during staging; original untouched."
            )
        _reject_busy_source(source)
        if _file_digest(source) != before_digest:
            raise RebuildRefused(
                "Source database bytes changed during staging; original untouched."
            )
        backup = source.with_name(
            f"{source.name}.before-memory-rebuild-{uuid4().hex[:12]}.bak"
        )
        shutil.copy2(source, backup)
        if _file_digest(backup) != before_digest:
            raise RebuildRefused("Backup verification failed; source untouched.")
        os.replace(stage, source)
        return RebuildResult(names, events, fts, vec, policy, backup)
    finally:
        try:
            stage.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", ".lock"):
                Path(str(stage) + suffix).unlink(missing_ok=True)
        finally:
            release_writer_lock(lock)
