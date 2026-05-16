"""Host-side driver for the chunked ``terrarium.files`` write_stream.

The worker-side handler lives in
:mod:`kohakuterrarium.laboratory.adapters.terrarium_files`
(``write_begin`` / ``write_chunk`` / ``write_commit`` / ``write_abort``).
This module is the *other half* — the code a host (or any Lab node)
runs to push an arbitrarily-large payload across the link without any
single APP message approaching the transport frame ceiling.

Why this exists: a one-shot ``terrarium.files.write`` of a large
``.kohakutr`` (resume-onto-worker) or a creature bundle would overflow
the websocket frame and silently drop the connection.  Chunking is the
"pack system" — the payload is split into bounded sequential slices,
reassembled into a staging file on the worker, hash-verified, then
atomically committed.
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
    """One ``terrarium.files`` RPC, raising on the structured error envelope."""
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
    """Push ``data`` to ``to_node`` as a chunked ``write_stream``.

    Drives the ``write_begin`` / ``write_chunk`` / ``write_commit``
    handshake — no single APP message carries more than one
    ``chunk_size`` slice, so an arbitrarily large payload crosses the
    Lab link without ever approaching the transport frame ceiling.
    ``sender`` is any object exposing the Lab ``request`` coroutine
    (a ``HostEngine`` or a ``ClientConnector``).  On any mid-stream
    failure the transfer is aborted so the worker never keeps an orphan
    staging file.  Returns the ``write_commit`` response
    (``{"written", "sha256"}``).
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
    # Honour the worker's advertised chunk size if it is smaller.
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
        # An empty payload sends zero chunks — commit still closes it.
        return await _stream_request(
            sender, to_node, "write_commit", {"transfer_id": transfer_id}, timeout
        )
    except Exception:
        # Best-effort abort so the worker drops the staging file.
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
