"""Host-side driver for chunked ``terrarium.files`` writes.

Large payloads are split below the transport frame limit, reassembled in a
worker staging file, hash-verified, and atomically committed.
"""

import base64
import hashlib
from typing import Any

from kohakuterrarium.laboratory.adapters.terrarium_files import (
    STREAM_CHUNK_BYTES,
    TerrariumFilesAdapter,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

NAMESPACE = TerrariumFilesAdapter.NAMESPACE


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
    begin = await _stream_request(
        sender,
        to_node,
        "write_begin",
        {
            "scope": scope,
            "path": rel,
            "total_size": len(data),
            "sha256": _sha256_hex(data),
            "expect_hash": expect_hash,
        },
        timeout,
    )
    transfer_id = begin.get("transfer_id")
    if not isinstance(transfer_id, str) or not transfer_id:
        raise RuntimeError("write_begin returned no transfer_id")
    # The worker's lower limit governs to keep every chunk acceptable.
    step = chunk_size
    server_chunk = begin.get("chunk_size")
    if isinstance(server_chunk, int) and 0 < server_chunk < step:
        step = server_chunk
    step = max(step, 1)
    try:
        seq = 0
        for offset in range(0, len(data), step):
            piece = data[offset : offset + step]
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
    except Exception:
        # Preserve the original failure even if staging cleanup also fails.
        try:
            await sender.request(
                to_node=to_node,
                namespace=NAMESPACE,
                type="write_abort",
                body={"transfer_id": transfer_id},
                timeout=10.0,
            )
        except Exception:  # pragma: no cover - best effort
            pass
        raise


__all__ = ["NAMESPACE", "stream_write_file"]
