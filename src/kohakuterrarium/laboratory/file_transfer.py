"""Host-side driver for chunked ``terrarium.files`` writes.

Large payloads are split below the transport frame limit, reassembled in a
worker staging file, hash-verified, and atomically committed.
"""

import base64
import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import aiofiles

from kohakuterrarium.laboratory.adapters.terrarium.files import (
    STREAM_CHUNK_BYTES,
    TerrariumFilesAdapter,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

NAMESPACE = TerrariumFilesAdapter.NAMESPACE


class FileTransferSourceError(OSError):
    """A local source file could not be opened, read or rewound."""


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _stream_request(
    sender: Any, to_node: str, type_: str, body: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """Issue one file RPC and raise errors returned in its response envelope."""
    resp = await sender.request(
        to_node=to_node,
        namespace=NAMESPACE,
        type=type_,
        body=body,
        timeout=timeout,
    )
    if isinstance(resp, dict) and "error" in resp:
        err = resp["error"]
        message = err.get("message", err) if isinstance(err, dict) else err
        raise RuntimeError(f"terrarium.files {type_} failed: {message}")
    return resp if isinstance(resp, dict) else {}


async def stream_write_file(
    sender: Any,
    to_node: str,
    scope: str,
    rel: str,
    data: bytes,
    *,
    expect_hash: str | None = None,
    chunk_size: int = STREAM_CHUNK_BYTES,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Write data to a remote file through the chunked transfer handshake.

    Each message carries at most one chunk. Failures trigger a best-effort abort
    to discard the worker's staging file. The commit response contains
    ``written`` and ``sha256``.
    """

    async def chunks(step: int) -> AsyncIterator[bytes]:
        for offset in range(0, len(data), step):
            yield data[offset : offset + step]

    return await _stream_write(
        sender,
        to_node,
        scope,
        rel,
        len(data),
        _sha256_hex(data),
        chunks,
        expect_hash=expect_hash,
        chunk_size=chunk_size,
        timeout=timeout,
    )


async def _file_chunks(stream: Any, step: int) -> AsyncIterator[bytes]:
    while True:
        try:
            piece = await stream.read(step)
        except OSError as exc:
            raise FileTransferSourceError(str(exc)) from exc
        if not piece:
            return
        yield piece


async def stream_write_path(
    sender: Any,
    to_node: str,
    scope: str,
    rel: str,
    source: Path,
    *,
    expect_hash: str | None = None,
    chunk_size: int = STREAM_CHUNK_BYTES,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Hash and transfer a local file in bounded chunks through one open handle."""
    async with AsyncExitStack() as resources:
        try:
            stream = await resources.enter_async_context(aiofiles.open(source, "rb"))
        except OSError as exc:
            raise FileTransferSourceError(str(exc)) from exc
        checksum = hashlib.sha256()
        size = 0
        async for piece in _file_chunks(stream, STREAM_CHUNK_BYTES):
            checksum.update(piece)
            size += len(piece)
        piece = b""
        try:
            await stream.seek(0)
        except OSError as exc:
            raise FileTransferSourceError(str(exc)) from exc

        async def chunks(step: int) -> AsyncIterator[bytes]:
            sent_hash = hashlib.sha256()
            sent_size = 0
            async for piece in _file_chunks(stream, step):
                sent_hash.update(piece)
                sent_size += len(piece)
                yield piece
            if sent_size != size or sent_hash.digest() != checksum.digest():
                raise RuntimeError("Source file changed during transfer")
            try:
                await resources.aclose()
            except OSError as exc:
                raise FileTransferSourceError(str(exc)) from exc

        return await _stream_write(
            sender,
            to_node,
            scope,
            rel,
            size,
            checksum.hexdigest(),
            chunks,
            expect_hash=expect_hash,
            chunk_size=chunk_size,
            timeout=timeout,
        )


async def _stream_write(
    sender: Any,
    to_node: str,
    scope: str,
    rel: str,
    total_size: int,
    checksum: str,
    chunks: Callable[[int], AsyncIterator[bytes]],
    *,
    expect_hash: str | None,
    chunk_size: int,
    timeout: float,
) -> dict[str, Any]:
    """Negotiate bounded chunks, commit the stream and abort known failed transfers."""
    begin = await _stream_request(
        sender,
        to_node,
        "write_begin",
        {
            "scope": scope,
            "path": rel,
            "total_size": total_size,
            "sha256": checksum,
            "expect_hash": expect_hash,
        },
        timeout,
    )
    transfer_id = begin.get("transfer_id")
    if not isinstance(transfer_id, str) or not transfer_id:
        raise RuntimeError("write_begin returned no transfer_id")
    try:
        step = min(max(chunk_size, 1), STREAM_CHUNK_BYTES)
        server_chunk = begin.get("chunk_size")
        if isinstance(server_chunk, int) and 0 < server_chunk < step:
            step = server_chunk
        seq = 0
        async for piece in chunks(step):
            await _stream_request(
                sender,
                to_node,
                "write_chunk",
                {
                    "transfer_id": transfer_id,
                    "seq": seq,
                    "bytes_b64": _b64encode(piece),
                },
                timeout,
            )
            seq += 1
        # Empty transfers have no chunks but still require commit.
        return await _stream_request(
            sender, to_node, "write_commit", {"transfer_id": transfer_id}, timeout
        )
    except BaseException:
        # Preserve the original failure even if staging cleanup also fails.
        try:
            await sender.request(
                to_node=to_node,
                namespace=NAMESPACE,
                type="write_abort",
                body={"transfer_id": transfer_id},
                timeout=10.0,
            )
        except BaseException:  # pragma: no cover - best effort
            pass
        raise


__all__ = [
    "NAMESPACE",
    "FileTransferSourceError",
    "stream_write_file",
    "stream_write_path",
]
