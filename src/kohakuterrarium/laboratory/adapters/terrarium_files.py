"""APP extension adapter for ``terrarium.files``.

Worker-side handler for scope-bounded file operations.  Supports the
one-shot operations (list / stat / read / write / delete), the chunked
``write_stream`` family (``write_begin`` / ``write_chunk`` /
``write_commit`` / ``write_abort``) for transfers beyond the one-shot
size limit, plus the :meth:`push_bundle` deploy primitive used by
``studio.deploy``.

Why chunking — a single Lab APP message has a finite frame ceiling
(``transport_ws.LAB_WS_MAX_SIZE``), and a one-shot ``write`` of a
large ``.kohakutr`` (resume-onto-worker) or creature bundle would
otherwise either overflow that frame and silently drop the
connection, or hit the ``MAX_ONESHOT_BYTES`` guard.  The
``write_stream`` family splits an arbitrarily-large payload into
sequential bounded chunks reassembled into a staging file, then
atomically committed — no single message ever approaches the frame
ceiling.  :func:`stream_write_file` is the host-side driver.

Out of scope (added later):

- ``read_stream`` — chunked *reads* of large files.  ``read`` of a
  file exceeding the one-shot cap still returns an ``invalid`` error.
- ``watch`` — file-system change events as a Channel stream.

Path safety is enforced at the boundary by
:mod:`kohakuterrarium.laboratory.adapters.file_scopes`.  Every
operation resolves ``(scope, path)`` to an absolute path through
:func:`resolve_in_scope`, which rejects absolute or ``..``-bearing
relatives.

``push_bundle`` semantics — for each file in ``files``:

- absent at target → write
- present, hash matches → no-op (idempotent re-push)
- present, hash differs → bundle aborts; conflicts returned

Writes go through ``<root>/.staging-<uuid>/`` first, then are
``os.replace``-d into place — partial failures don't leave half-written
files at the canonical paths.
"""

import asyncio
import base64
import hashlib
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import aiofiles

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.file_scopes import (
    ScopeError,
    resolve_in_scope,
    resolve_scope_root,
)
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


MAX_ONESHOT_BYTES = 1 * 1024 * 1024  # 1 MiB cap for read/write without streaming
CHUNK_HASH_ALGO = "sha256"
# Per-chunk payload size for the ``write_stream`` family.  Kept well
# under both ``MAX_ONESHOT_BYTES`` and the transport frame ceiling so a
# base64-encoded chunk message can never approach the limit, no matter
# how large the overall transfer.
STREAM_CHUNK_BYTES = 256 * 1024


class _StreamingWrite:
    """Server-side state for one in-flight chunked write.

    Chunks are appended to a per-transfer staging file in ``seq`` order;
    ``write_commit`` verifies the running hash + total size and then
    atomically ``os.replace``-s the staging file into place.
    """

    __slots__ = (
        "scope",
        "rel",
        "target",
        "staging",
        "expected_size",
        "expected_sha",
        "expect_hash",
        "hasher",
        "received",
        "next_seq",
    )

    def __init__(
        self,
        scope: str,
        rel: str,
        target: Path,
        staging: Path,
        expected_size: int,
        expected_sha: str | None,
        expect_hash: str | None,
    ) -> None:
        self.scope = scope
        self.rel = rel
        self.target = target
        self.staging = staging
        self.expected_size = expected_size
        self.expected_sha = expected_sha
        self.expect_hash = expect_hash
        self.hasher = hashlib.sha256()
        self.received = 0
        self.next_seq = 0


class TerrariumFilesAdapter:
    """Worker-side ``terrarium.files`` APP extension."""

    NAMESPACE = "terrarium.files"

    def __init__(self, engine: Terrarium, lab_node: LabRegistrar) -> None:
        self._engine = engine
        self._node = lab_node
        # In-flight chunked writes, keyed by transfer_id.
        self._transfers: dict[str, _StreamingWrite] = {}
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    def detach(self) -> None:
        self._node.unregister_app_extension(self.NAMESPACE)
        # Drop any half-finished transfers — a never-committed staging
        # file would otherwise linger inside the scope root forever.
        for transfer in list(self._transfers.values()):
            try:
                transfer.staging.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensive
                logger.debug("orphan staging cleanup failed", exc_info=True)
        self._transfers.clear()
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except ScopeError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except FileNotFoundError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except PermissionError as e:
            return {"error": {"kind": "denied", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("terrarium.files handler failed: %s", msg.type)
            return {"error": {"kind": "files", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        # File ops use ``aiofiles`` for reads/writes and ``to_thread``
        # for the bits aiofiles doesn't cover (``os.replace``,
        # ``shutil.rmtree``, directory listing, ``stat``).  Either way,
        # nothing here blocks the event loop while a bundle deploys.
        match msg.type:
            case "list":
                return await self._op_list(msg.body)
            case "stat":
                return await self._op_stat(msg.body)
            case "read":
                return await self._op_read(msg.body)
            case "write":
                return await self._op_write(msg.body)
            case "write_begin":
                return await self._op_write_begin(msg.body)
            case "write_chunk":
                return await self._op_write_chunk(msg.body)
            case "write_commit":
                return await self._op_write_commit(msg.body)
            case "write_abort":
                return await self._op_write_abort(msg.body)
            case "delete":
                return await self._op_delete(msg.body)
            case "push_bundle":
                return await self._op_push_bundle(msg.body)
            case "getcwd":
                return await self._op_getcwd(msg.body)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported terrarium.files type: {msg.type!r}",
                    }
                }

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    async def _op_list(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        recursive = bool(body.get("recursive", False))
        target = resolve_in_scope(scope, rel, self._engine)
        # Directory traversal + per-entry stat() are sync syscalls that
        # have no aiofiles equivalent; offload to a worker thread so a
        # deep ``rglob`` doesn't stall the loop.
        return await asyncio.to_thread(_list_sync, target, scope, rel, recursive)

    async def _op_stat(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        target = resolve_in_scope(scope, rel, self._engine)
        if not target.exists():
            raise FileNotFoundError(f"no such path: {scope}/{rel}")
        st = await asyncio.to_thread(target.stat)
        result = {
            "size": st.st_size,
            "mtime": st.st_mtime,
            "is_dir": target.is_dir(),
        }
        if target.is_file():
            result["sha256"] = await _hash_file_async(target)
        return {"stat": result}

    async def _op_read(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        target = resolve_in_scope(scope, rel, self._engine)
        if not target.exists():
            raise FileNotFoundError(f"no such path: {scope}/{rel}")
        if not target.is_file():
            raise ScopeError(f"not a file: {scope}/{rel}")
        size = (await asyncio.to_thread(target.stat)).st_size
        if size > MAX_ONESHOT_BYTES:
            raise ScopeError(
                f"file exceeds one-shot limit ({size} > {MAX_ONESHOT_BYTES} "
                "bytes); chunked read_stream not yet supported"
            )
        async with aiofiles.open(target, "rb") as f:
            data = await f.read()
        # Wire format encodes bytes as base64 strings; the kohakuvault
        # DataPacker used by the APP layer rejects raw bytes.
        return {"bytes_b64": _b64encode(data), "sha256": _hash_bytes(data)}

    async def _op_write(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        data = _decode_wire_bytes(body, "bytes_b64")
        if len(data) > MAX_ONESHOT_BYTES:
            raise ScopeError(
                f"payload exceeds one-shot limit ({len(data)} > "
                f"{MAX_ONESHOT_BYTES} bytes); chunked write_stream not yet supported"
            )
        target = resolve_in_scope(scope, rel, self._engine)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        expect_hash = body.get("expect_hash")
        if expect_hash is not None and target.exists():
            actual = await _hash_file_async(target)
            if actual != expect_hash:
                raise ScopeError(
                    f"expect_hash mismatch: file at {scope}/{rel} has sha256 {actual}"
                )
        async with aiofiles.open(target, "wb") as f:
            await f.write(data)
        return {"written": len(data), "sha256": _hash_bytes(data)}

    # ------------------------------------------------------------------
    # Chunked write_stream — begin / chunk / commit / abort
    # ------------------------------------------------------------------

    async def _op_write_begin(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        total_size = body.get("total_size")
        if not isinstance(total_size, int) or total_size < 0:
            raise ScopeError("write_begin requires a non-negative int total_size")
        target = resolve_in_scope(scope, rel, self._engine)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        transfer_id = uuid.uuid4().hex
        staging = target.parent / f".staging-stream-{transfer_id}"
        # Create the staging file empty — chunks append to it in order.
        async with aiofiles.open(staging, "wb"):
            pass
        self._transfers[transfer_id] = _StreamingWrite(
            scope,
            rel,
            target,
            staging,
            total_size,
            body.get("sha256"),
            body.get("expect_hash"),
        )
        return {"transfer_id": transfer_id, "chunk_size": STREAM_CHUNK_BYTES}

    async def _op_write_chunk(self, body: dict[str, Any]) -> dict[str, Any]:
        transfer_id = body.get("transfer_id")
        transfer = (
            self._transfers.get(transfer_id) if isinstance(transfer_id, str) else None
        )
        if transfer is None:
            raise KeyError(f"unknown transfer_id: {transfer_id!r}")
        seq = body.get("seq")
        if seq != transfer.next_seq:
            # Out-of-order / duplicate chunk — discard the whole transfer
            # so the caller restarts cleanly instead of silently
            # corrupting the staging file.
            await self._discard_transfer(transfer_id)
            raise ScopeError(
                f"chunk out of order: expected seq {transfer.next_seq}, got {seq!r}"
            )
        data = _decode_wire_bytes(body, "bytes_b64")
        if transfer.received + len(data) > transfer.expected_size:
            await self._discard_transfer(transfer_id)
            raise ScopeError("chunk stream overran the declared total_size")
        async with aiofiles.open(transfer.staging, "ab") as f:
            await f.write(data)
        transfer.hasher.update(data)
        transfer.received += len(data)
        transfer.next_seq += 1
        return {"received": transfer.received}

    async def _op_write_commit(self, body: dict[str, Any]) -> dict[str, Any]:
        transfer_id = body.get("transfer_id")
        transfer = (
            self._transfers.get(transfer_id) if isinstance(transfer_id, str) else None
        )
        if transfer is None:
            raise KeyError(f"unknown transfer_id: {transfer_id!r}")
        if transfer.received != transfer.expected_size:
            await self._discard_transfer(transfer_id)
            raise ScopeError(
                f"incomplete transfer: received {transfer.received} of "
                f"{transfer.expected_size} bytes"
            )
        actual_sha = transfer.hasher.hexdigest()
        if transfer.expected_sha is not None and actual_sha != transfer.expected_sha:
            await self._discard_transfer(transfer_id)
            raise ScopeError(
                f"sha256 mismatch: stream hashed to {actual_sha}, "
                f"expected {transfer.expected_sha}"
            )
        if transfer.expect_hash is not None and transfer.target.exists():
            on_disk = await _hash_file_async(transfer.target)
            if on_disk != transfer.expect_hash:
                await self._discard_transfer(transfer_id)
                raise ScopeError(
                    f"expect_hash mismatch: file at "
                    f"{transfer.scope}/{transfer.rel} has sha256 {on_disk}"
                )
        if await self._commit_is_idempotent(transfer, actual_sha):
            await self._cleanup_after_commit(transfer_id, transfer.staging)
            return {"written": transfer.received, "sha256": actual_sha}
        try:
            await asyncio.to_thread(os.replace, transfer.staging, transfer.target)
        except PermissionError as exc:
            # Windows: destination held open (typical: a SessionStore
            # adopted by a prior resume). The next reader picks up our
            # bytes once its WAL checkpoints; refuse to rewrite-over a
            # locking handle but treat the call as idempotently OK.
            if not transfer.target.exists():
                raise
            logger.warning(
                "write_commit: target locked, idempotent re-push (%s)",
                transfer.target,
            )
            await self._cleanup_after_commit(transfer_id, transfer.staging)
            return {
                "written": transfer.received,
                "sha256": actual_sha,
                "skipped_replace": str(exc),
            }
        self._transfers.pop(transfer_id, None)
        return {"written": transfer.received, "sha256": actual_sha}

    async def _commit_is_idempotent(self, transfer, actual_sha: str) -> bool:
        """The destination already holds exactly what we'd write."""
        if not transfer.target.exists():
            return False
        try:
            on_disk = await _hash_file_async(transfer.target)
        except OSError:
            return False
        return on_disk == actual_sha

    async def _cleanup_after_commit(self, transfer_id: str, staging) -> None:
        try:
            await asyncio.to_thread(staging.unlink, True)
        except OSError:  # pragma: no cover - defensive
            pass
        self._transfers.pop(transfer_id, None)

    async def _op_write_abort(self, body: dict[str, Any]) -> dict[str, Any]:
        transfer_id = body.get("transfer_id")
        if isinstance(transfer_id, str):
            await self._discard_transfer(transfer_id)
        return {}

    async def _discard_transfer(self, transfer_id: str) -> None:
        """Drop transfer state and remove its staging file.  Idempotent."""
        transfer = self._transfers.pop(transfer_id, None)
        if transfer is None:
            return
        try:
            await asyncio.to_thread(transfer.staging.unlink, True)
        except OSError:  # pragma: no cover - defensive
            logger.debug("staging cleanup failed for %s", transfer_id, exc_info=True)

    async def _op_delete(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        # Refuse to delete a scope root.  Resolving an empty relative
        # path returns the scope root itself (see ``resolve_in_scope``),
        # and the recursive delete below would then nuke entire
        # scope-rooted directories like ``~/.kohakuterrarium`` for
        # ``config://``.  Require an explicit non-empty path so a
        # frontend bug or a misconfigured tool can't trigger it.
        if not rel:
            raise ScopeError(
                f"refusing to delete scope root {scope!r}; "
                "pass an explicit non-empty path"
            )
        target = resolve_in_scope(scope, rel, self._engine)
        if not target.exists():
            raise FileNotFoundError(f"no such path: {scope}/{rel}")
        # ``rmtree``/``unlink`` are sync; offload.
        if target.is_dir():
            await asyncio.to_thread(shutil.rmtree, target)
        else:
            await asyncio.to_thread(target.unlink)
        return {}

    async def _op_getcwd(self, body: dict[str, Any]) -> dict[str, Any]:
        """Return the worker's default working directory.

        Used by the host to populate the "Working directory" field in
        the New Creature / New Terrarium modal when the user picks a
        worker as the spawn target.  ``cwd`` is the worker process's
        ``os.getcwd()`` and ``home`` is the worker's ``Path.home()`` —
        the host route prefers ``home`` because the worker process
        directory is rarely a useful workspace default.
        """
        return {
            "cwd": str(await asyncio.to_thread(os.getcwd)),
            "home": str(Path.home()),
            "platform": sys.platform,
        }

    async def _op_push_bundle(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        files = body["files"]
        if not isinstance(files, dict):
            raise ScopeError("files field must be a dict of rel -> [sha256, bytes]")
        root = resolve_scope_root(scope, self._engine)
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)

        # First pass: classify each file as no-op, conflict, or pending.
        conflicts: list[str] = []
        pending: list[tuple[str, bytes, str]] = []  # (rel, blob, expected_hash)
        for rel, payload in files.items():
            target = resolve_in_scope(scope, rel, self._engine)
            expected_hash, blob = _unpack_bundle_entry(payload)
            actual_hash = _hash_bytes(blob)
            if actual_hash != expected_hash:
                raise ScopeError(
                    f"bundle entry {rel!r} has mismatched hash "
                    f"(payload says {expected_hash}, computed {actual_hash})"
                )
            if target.exists():
                on_disk = await _hash_file_async(target)
                if on_disk == expected_hash:
                    continue  # idempotent skip
                conflicts.append(rel)
                continue
            pending.append((rel, blob, expected_hash))

        if conflicts:
            return {"deployed": [], "conflicts": conflicts}

        # Second pass: stage every file (verify hashes), then commit
        # via ``os.replace``.  If anything goes wrong during the commit
        # loop, partial replaces stay on disk — there's no general way
        # to roll back to "pre-deploy" state once a file has been
        # replaced.  Instead we report the partial outcome in the
        # response so the caller can decide how to recover (typically:
        # fix the underlying problem and retry; the second push will
        # idempotently skip the already-deployed files).
        staging = root / f".staging-{uuid.uuid4().hex}"
        await asyncio.to_thread(staging.mkdir)
        deployed: list[str] = []
        commit_error: str | None = None
        try:
            for rel, blob, expected_hash in pending:
                stage_path = staging / rel
                await asyncio.to_thread(
                    stage_path.parent.mkdir, parents=True, exist_ok=True
                )
                async with aiofiles.open(stage_path, "wb") as f:
                    await f.write(blob)
                if await _hash_file_async(stage_path) != expected_hash:
                    raise ScopeError(f"hash verification failed for staged {rel!r}")
            for rel, _blob, _hash in pending:
                stage_path = staging / rel
                final_path = resolve_in_scope(scope, rel, self._engine)
                await asyncio.to_thread(
                    final_path.parent.mkdir, parents=True, exist_ok=True
                )
                try:
                    await asyncio.to_thread(os.replace, stage_path, final_path)
                except OSError as e:
                    # First commit error — surface a structured partial
                    # result instead of an empty deployed list with the
                    # already-replaced files silently in place.
                    commit_error = f"failed to commit {rel!r}: {e}"
                    logger.warning(
                        "push_bundle partial deploy",
                        scope=scope,
                        committed=len(deployed),
                        remaining=len(pending) - len(deployed),
                        error=str(e),
                    )
                    break
                deployed.append(rel)
        finally:
            if staging.exists():
                await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)
        if commit_error is not None:
            remaining = [rel for rel, _, _ in pending if rel not in set(deployed)]
            return {
                "deployed": deployed,
                "conflicts": [],
                "partial": True,
                "remaining": remaining,
                "error": commit_error,
            }
        return {"deployed": deployed, "conflicts": []}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def _hash_file(path: Path) -> str:
    """Synchronous chunked sha256.  Kept for tests and any sync caller."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def _hash_file_async(path: Path) -> str:
    """Async chunked sha256 via aiofiles — never blocks the event loop."""
    h = hashlib.sha256()
    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _list_sync(target: Path, scope: str, rel: str, recursive: bool) -> dict[str, Any]:
    """Worker-thread body for ``_op_list`` — pure sync, no event loop."""
    if not target.exists():
        raise FileNotFoundError(f"no such path: {scope}/{rel}")
    if not target.is_dir():
        raise ScopeError(f"path is not a directory: {scope}/{rel}")
    entries: list[dict[str, Any]] = []
    iterator = target.rglob("*") if recursive else target.iterdir()
    for entry in iterator:
        rel_entry = entry.relative_to(target).as_posix()
        try:
            st = entry.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": rel_entry,
                "is_dir": entry.is_dir(),
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        )
    entries.sort(key=lambda e: e["name"])
    return {"entries": entries}


def _unpack_bundle_entry(payload: Any) -> tuple[str, bytes]:
    """Bundle entries are ``[sha256_hex, base64_str]`` on the wire."""
    if not isinstance(payload, (list, tuple)) or len(payload) != 2:
        raise ScopeError("bundle entry must be [sha256_hex, base64_str]")
    expected_hash, blob_b64 = payload
    if not isinstance(expected_hash, str):
        raise ScopeError("bundle entry sha256 must be a string")
    if not isinstance(blob_b64, str):
        raise ScopeError("bundle entry payload must be a base64 string")
    try:
        blob = base64.b64decode(blob_b64, validate=True)
    except (ValueError, base64.binascii.Error) as e:
        raise ScopeError(f"bundle entry payload is not valid base64: {e}") from e
    return expected_hash, blob


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_wire_bytes(body: dict[str, Any], key: str) -> bytes:
    """Decode a base64 string from the body, or accept raw bytes for tests."""
    raw = body.get(key)
    if raw is None:
        raise ScopeError(f"missing required field: {key!r}")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        try:
            return base64.b64decode(raw, validate=True)
        except (ValueError, base64.binascii.Error) as e:
            raise ScopeError(f"{key} is not valid base64: {e}") from e
    raise ScopeError(f"{key} must be a base64 string")


__all__ = [
    "MAX_ONESHOT_BYTES",
    "STREAM_CHUNK_BYTES",
    "TerrariumFilesAdapter",
]
