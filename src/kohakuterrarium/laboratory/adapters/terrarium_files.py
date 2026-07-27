"""Serve scope-bounded file operations and chunked writes on workers."""

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


MAX_ONESHOT_BYTES = 1 * 1024 * 1024  # Larger writes must use staged chunks.
CHUNK_HASH_ALGO = "sha256"
# Leave headroom for base64 and APP envelope overhead below transport limits.
STREAM_CHUNK_BYTES = 256 * 1024


class _StreamingWrite:
    """Track ordered chunks and integrity metadata for a staged write."""

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
        self._transfers: dict[str, _StreamingWrite] = {}
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    def detach(self) -> None:
        self._node.unregister_app_extension(self.NAMESPACE)
        # Remove staging files that were never committed.
        for transfer in list(self._transfers.values()):
            try:
                transfer.staging.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - defensive
                logger.warning("orphan staging cleanup failed", exc_info=True)
        self._transfers.clear()
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    def _reject_active_session_path(
        self,
        target: Path,
        *,
        include_descendants: bool = False,
    ) -> None:
        """Prevent generic file operations from mutating attached stores."""
        resolved = target.expanduser().resolve(strict=False)
        stores = getattr(self._engine, "_session_stores", {}) or {}
        for store in stores.values():
            store_path = Path(store.path).expanduser().resolve(strict=False)
            matches = store_path == resolved
            if include_descendants and not matches:
                try:
                    store_path.relative_to(resolved)
                    matches = True
                except ValueError:
                    pass
            if matches:
                raise ScopeError("operation refuses an active session store")

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

    async def _op_list(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        recursive = bool(body.get("recursive", False))
        target = resolve_in_scope(scope, rel, self._engine)
        # Directory traversal and per-entry metadata calls are synchronous.
        return await asyncio.to_thread(_list_sync, target, scope, rel, recursive)

    async def _op_stat(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        target = resolve_in_scope(scope, rel, self._engine)
        if not target.exists():
            raise FileNotFoundError(f"no such path: {scope}/{rel}")
        st = await asyncio.to_thread(target.stat)
        result = {
            "path": str(target),
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
        # APP msgpack payloads cannot carry raw bytes, so encode them as base64.
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
        self._reject_active_session_path(target)
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

    async def _op_write_begin(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        total_size = body.get("total_size")
        if not isinstance(total_size, int) or total_size < 0:
            raise ScopeError("write_begin requires a non-negative int total_size")
        target = resolve_in_scope(scope, rel, self._engine)
        self._reject_active_session_path(target)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        transfer_id = uuid.uuid4().hex
        staging = target.parent / f".staging-stream-{transfer_id}"
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
            # Discard on sequence errors rather than retain ambiguous staged bytes.
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
        try:
            self._reject_active_session_path(transfer.target)
        except ScopeError:
            await self._discard_transfer(transfer_id)
            raise
        if await self._commit_is_idempotent(transfer, actual_sha):
            await self._cleanup_after_commit(transfer_id, transfer.staging)
            return {"written": transfer.received, "sha256": actual_sha}
        try:
            await asyncio.to_thread(os.replace, transfer.staging, transfer.target)
        except PermissionError as exc:
            # Windows may lock a resumed session target; retain idempotent success.
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
        """Return whether the destination already contains the staged content."""
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
        """Idempotently remove transfer state and its staging file."""
        transfer = self._transfers.pop(transfer_id, None)
        if transfer is None:
            return
        try:
            await asyncio.to_thread(transfer.staging.unlink, True)
        except OSError:  # pragma: no cover - defensive
            logger.warning("staging cleanup failed for %s", transfer_id, exc_info=True)

    async def _op_delete(self, body: dict[str, Any]) -> dict[str, Any]:
        scope = body["scope"]
        rel = body.get("path", "")
        # Empty paths resolve to the scope root, which must never be deleted.
        if not rel:
            raise ScopeError(
                f"refusing to delete scope root {scope!r}; "
                "pass an explicit non-empty path"
            )
        target = resolve_in_scope(scope, rel, self._engine)
        self._reject_active_session_path(target, include_descendants=True)
        if not target.exists():
            raise FileNotFoundError(f"no such path: {scope}/{rel}")
        if target.is_dir():
            await asyncio.to_thread(shutil.rmtree, target)
        else:
            await asyncio.to_thread(target.unlink)
        return {}

    async def _op_getcwd(self, body: dict[str, Any]) -> dict[str, Any]:
        """Return worker process, home, and platform defaults for spawn forms."""
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

        # Detect all conflicts before writing any bundle file.
        conflicts: list[str] = []
        pending: list[tuple[str, bytes, str]] = []
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
                    continue
                conflicts.append(rel)
                continue
            pending.append((rel, blob, expected_hash))

        if conflicts:
            return {"deployed": [], "conflicts": conflicts}

        # Replacements cannot be rolled back generally; report partial commits for retry.
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
                    # Preserve the committed prefix in the error response.
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


def _hash_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def _hash_file(path: Path) -> str:
    """Hash a file synchronously in bounded chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def _hash_file_async(path: Path) -> str:
    """Hash a file asynchronously in bounded chunks."""
    h = hashlib.sha256()
    async with aiofiles.open(path, "rb") as f:
        while True:
            chunk = await f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _list_sync(target: Path, scope: str, rel: str, recursive: bool) -> dict[str, Any]:
    """List directory entries synchronously for worker-thread execution."""
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
    """Decode a bundle entry containing a digest and base64 payload."""
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
    """Decode wire-safe base64, accepting raw bytes for direct callers."""
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
