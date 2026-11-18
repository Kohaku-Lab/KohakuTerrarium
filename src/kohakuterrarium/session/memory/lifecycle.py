"""Session-wide index format, migration and published-read lifecycle.

Memory index validity is independent from the original SessionStore event log.
Physical leftovers may exist while invalid, but never participate in queries.
"""

from contextlib import contextmanager

from kohakuvault import TextVault

from kohakuterrarium.session.memory.blocks import _anchor
from kohakuterrarium.session.memory.query import _ALL_BRANCH_INDEX_FORMAT
from kohakuterrarium.session.vault.io import key_text, scan_keys
from kohakuterrarium.utils.file.lock import FileLock, FileLockBusy


class MemoryIndexLifecycle:
    """Coordinated session-level FTS/vector states, shared by all agents."""

    @contextmanager
    def _mutation(self):
        """Serialize index mutations, without locking across model encoding."""
        lock = FileLock(f"{self._path}.memory-index.lock")
        try:
            lock.acquire()
        except FileLockBusy as exc:
            raise RuntimeError("Memory index is being modified; retry") from exc
        try:
            yield
        finally:
            lock.release()

    def _claim_build(self, kind: str) -> None:
        attr = f"_{kind}_build_lock"
        if getattr(self, attr) is not None:
            return
        lock = FileLock(f"{self._path}.memory-{kind}-build.lock")
        try:
            lock.acquire()
        except FileLockBusy as exc:
            raise RuntimeError(
                f"Session memory {kind} rebuild is already in progress; retry"
            ) from exc
        setattr(self, attr, lock)

    def _release_build(self, kind: str) -> None:
        attr = f"_{kind}_build_lock"
        lock = getattr(self, attr)
        if lock is not None:
            lock.release()
            setattr(self, attr, None)

    def _release_build_locks(self) -> None:
        self._release_build("vector")
        self._release_build("fts")

    def _manifest(self) -> dict | None:
        value = self._state.get("memory:index_manifest")
        return value if isinstance(value, dict) else None

    def _known_agents(self) -> set[str]:
        """Discover legacy indexed namespaces before deleting shared rows."""
        suffixes = (
            ":cursor:keywords",
            ":indexed_keywords",
            ":indexed_events",
            ":index_format",
        )
        return {
            name[: -len(suffix)]
            for key in scan_keys(self._state)
            for name in [key_text(key)]
            for suffix in suffixes
            if name.endswith(suffix)
        }

    def _save_manifest(self, manifest: dict) -> None:
        self._state["memory:index_manifest"] = manifest

    def _ensure_index_format(self, agent: str) -> dict:
        """Invalidate the whole Session index before a format transition."""
        current = self._manifest()
        if current and current.get("format", 0) > self._index_format:
            raise ValueError("Cannot downgrade a session memory index format")
        restart = (
            current is None
            or current.get("format") != self._index_format
            or (current.get("fts") == "building" and self._fts_build_lock is None)
        )
        if restart:
            self._claim_build("fts")
            known = self._known_agents() | set((current or {}).get("agents", ()))
            return self._reset_session_indexes(known | {agent}, current)
        return current

    def _reset_session_indexes(self, agents: set[str], current: dict | None) -> dict:
        """Create a new global epoch. Never publish legacy physical rows."""
        epoch = (current or {}).get("epoch", 0) + 1
        manifest = {
            "format": self._index_format,
            "epoch": epoch,
            "fts_revision": (current or {}).get("fts_revision", 0) + 1,
            "vector_revision": (current or {}).get("vector_revision", 0) + 1,
            # Tracks destructive vector-table replacement independently of
            # ordinary writes or the session-wide FTS index epoch.
            "vector_generation": (current or {}).get("vector_generation", 0),
            "fts": "building",
            "fts_pending": sorted(agents),
            "agents": sorted(agents),
            "vector": "invalid",
            "vector_identity": None,
            "vector_dimensions": None,
            "vector_pending": [],
            "vector_needs_clear": True,
        }
        self._save_manifest(manifest)
        self._fts.clear()
        self._fts_cleared_epoch = epoch
        return manifest

    def force_rebuild(self, agents: list[str]) -> None:
        """Invalidate both indexes globally before a forced re-embedding."""
        with self._mutation():
            self._claim_build("fts")
            self._reset_session_indexes(set(agents), self._manifest())

    def _begin_fts_append(self, agent: str, events: list[dict]) -> None:
        manifest = self._ensure_index_format(agent)
        if manifest["fts"] != "ready":
            return
        checkpoint = self._state.get(f"{agent}:cursor:keywords")
        if (
            not isinstance(checkpoint, dict)
            or checkpoint.get("index_epoch") != manifest["epoch"]
            or checkpoint.get("generation") != self._generation(agent)
            or checkpoint.get("count") != len(events)
            or (events and checkpoint.get("anchor") != _anchor(events[-1]))
            or self._state.get(f"{agent}:cursor:keywords:pending", False)
        ):
            pending = set(manifest.get("fts_pending", ()))
            pending.add(agent)
            self._claim_build("fts")
            manifest = dict(manifest, fts="building", fts_pending=sorted(pending))
            manifest["fts_revision"] = manifest.get("fts_revision", 0) + 1
            self._save_manifest(manifest)

    def _finish_fts(self, agent: str, changed: bool) -> None:
        manifest = dict(self._manifest())
        pending = set(manifest.get("fts_pending", ()))
        pending.discard(agent)
        manifest["fts_pending"] = sorted(pending)
        agents = set(manifest.get("agents", ()))
        agents.add(agent)
        manifest["agents"] = sorted(agents)
        finished = manifest.get("fts") == "building" and not pending
        if finished:
            manifest["fts"] = "ready"
        if changed:
            manifest["fts_revision"] = manifest.get("fts_revision", 0) + 1
        if changed and manifest["vector"] in ("ready", "dirty", "building"):
            if manifest["vector"] == "ready":
                manifest["vector"] = "dirty"
            manifest["vector_pending"] = sorted(
                set(manifest.get("vector_pending", ())) | {agent}
            )
        if changed:
            manifest["vector_revision"] = manifest.get("vector_revision", 0) + 1
        self._save_manifest(manifest)
        if finished:
            self._release_build("fts")

    def _assert_fts_readable(self) -> dict | None:
        manifest = self._manifest()
        if manifest is None and self._fts.count() == 0:
            return None
        if (
            manifest is None
            or manifest.get("format") != self._index_format
            or manifest.get("fts") != "ready"
        ):
            raise ValueError("Memory index invalid or building; rebuild before search")
        return manifest

    def _begin_vector(self, agent: str) -> None:
        manifest = dict(self._ensure_index_format(agent))
        dims = self._embedder.dimensions
        identity = self._vector_identity
        restart = (
            manifest.get("vector") == "invalid"
            or type(manifest.get("vector_generation")) is not int
            or manifest.get("vector_identity") != identity
            or manifest.get("vector_dimensions") != dims
            or manifest.get("vector_needs_clear", False)
            or (
                manifest.get("vector") == "building" and self._vector_build_lock is None
            )
        )
        if restart:
            self._claim_build("vector")
            # A pre-generation manifest cannot certify even a checkpoint
            # that happens to carry the next numeric token. Invalidate those
            # legacy cursors once before clearing the shared table.
            if type(manifest.get("vector_generation")) is not int:
                for name in set(manifest.get("agents", ())) | {agent}:
                    self._state.delete(f"{name}:cursor:vectors:{dims}")
            manifest["vector"] = "building"
            manifest["vector_identity"] = identity
            manifest["vector_dimensions"] = dims
            manifest["vector_pending"] = sorted(
                set(manifest.get("agents", ())) | {agent}
            )
            manifest["vector_needs_clear"] = True
            manifest["vector_generation"] = manifest.get("vector_generation", 0) + 1
            manifest["vector_revision"] = manifest.get("vector_revision", 0) + 1
            self._save_manifest(manifest)
            self._clear_vector_table()
            manifest["vector_needs_clear"] = False
            self._save_manifest(manifest)
        elif manifest.get("vector") in ("ready", "dirty"):
            if manifest["vector"] == "ready":
                # A completed vector cursor is a pure no-op: don't cycle
                # through Building for every idempotent index_events call.
                try:
                    self._assert_vector_readable(agent)
                except ValueError:
                    pass
                else:
                    return
            self._claim_build("vector")
            manifest["vector"] = "building"
            manifest["vector_pending"] = sorted(
                set(manifest.get("vector_pending", ())) | {agent}
            )
            manifest["vector_revision"] = manifest.get("vector_revision", 0) + 1
            self._save_manifest(manifest)

    def _finish_vector(self, agent: str) -> None:
        manifest = dict(self._manifest())
        pending = set(manifest.get("vector_pending", ()))
        keyword = self._state.get(f"{agent}:cursor:keywords")
        vector = self._state.get(f"{agent}:cursor:vectors:{self._embedder.dimensions}")
        complete = (
            isinstance(keyword, dict)
            and isinstance(vector, dict)
            and keyword.get("count") == vector.get("count")
            and vector.get("index_epoch") == manifest["epoch"]
            and vector.get("vector_generation") == manifest["vector_generation"]
        )
        if complete:
            pending.discard(agent)
        else:
            pending.add(agent)
        manifest["vector_pending"] = sorted(pending)
        if not pending and manifest.get("fts") == "ready":
            manifest["vector"] = "ready"
        manifest["vector_revision"] = manifest.get("vector_revision", 0) + 1
        self._save_manifest(manifest)
        if manifest["vector"] == "ready":
            self._release_build("vector")

    def _clear_vector_table(self) -> None:
        """Delete all physical rows, including orphaned checkpointless vectors."""
        if self._vec is None or self._vec.count() == 0:
            self._vec_cleared_epoch = self._manifest()["epoch"]
            return
        reader = self._resources.open(
            TextVault, self._path, table=f"memory_vec_{self._embedder.dimensions}d"
        )
        try:
            while self._vec.count():
                ids = reader.keys(limit=1024)
                if not ids:
                    raise RuntimeError("Cannot enumerate vector rows during rebuild")
                for row_id in ids:
                    self._vec.delete(row_id)
        finally:
            self._resources.drop(reader)
        self._vec_cleared_epoch = self._manifest()["epoch"]

    def _clear_index(self, agent: str, kind: str) -> None:
        vault = self._fts if kind == "keywords" else self._vec
        if kind == "keywords":
            ids = vault.keys(limit=vault.count())
        else:
            dims = int(kind.split(":")[1])
            # VectorKVault has no keys API. TextVault.keys performs a plain
            # rowid SELECT on the existing table through the same native SQLite
            # library; its CREATE IF NOT EXISTS leaves the vec0 table intact.
            # Python sqlite3 must not open a live vault: separate SQLite copies
            # can invalidate each other's WAL/locking state on POSIX.
            reader = self._resources.open(
                TextVault, self._path, table=f"memory_vec_{dims}d"
            )
            try:
                ids = reader.keys(limit=vault.count())
            finally:
                self._resources.drop(reader)
        for row_id in ids:
            _, meta = vault.get_by_id(row_id)
            if isinstance(meta, dict) and meta.get("agent") == agent:
                vault.delete(row_id)

    def _assert_vector_manifest_readable(self) -> dict:
        """Validate the global vector contract even with missing agent cursors."""
        manifest = self._manifest()
        dims = self._embedder.dimensions
        if (
            manifest is None
            or manifest.get("format") != self._index_format
            or manifest.get("fts") != "ready"
            or manifest.get("vector") != "ready"
            or type(manifest.get("vector_generation")) is not int
            or manifest.get("vector_identity") != self._vector_identity
            or manifest.get("vector_dimensions") != dims
        ):
            raise ValueError(
                "vector index stale or invalid; rebuild with its configured model"
            )
        return manifest

    def _assert_vector_readable(self, agent: str) -> None:
        """Validate per-agent cursor after the global vector contract."""
        manifest = self._assert_vector_manifest_readable()
        dims = self._embedder.dimensions
        key = f"{agent}:cursor:vectors:{dims}"
        checkpoint = self._state.get(key)
        indexed = self._state.get(f"{agent}:indexed_vectors:{dims}", 0)
        if checkpoint is None and not indexed:
            if agent not in manifest.get("agents", ()):
                return  # A genuinely unknown Agent cannot have a published corpus.
            raise ValueError(
                "vector index stale or missing Agent checkpoint; reindex this session"
            )
        keywords = self._state.get(f"{agent}:cursor:keywords")
        latest_count = (
            keywords["count"]
            if isinstance(keywords, dict)
            else self._get_indexed_count(agent)
        )
        if (
            not isinstance(checkpoint, dict)
            or checkpoint.get("generation") != self._generation(agent)
            or checkpoint.get("index_epoch") != manifest["epoch"]
            or checkpoint.get("vector_generation") != manifest["vector_generation"]
            or checkpoint.get("embedder_identity") != self._vector_identity
            or checkpoint.get("count") != latest_count
            or bool(checkpoint.get("all_branches", False))
            != (self._index_format == _ALL_BRANCH_INDEX_FORMAT)
            or self._state.get(f"{key}:pending", False)
        ):
            raise ValueError(
                "vector index stale or incompatible; reindex this session "
                "with its configured embedding model before semantic search"
            )
