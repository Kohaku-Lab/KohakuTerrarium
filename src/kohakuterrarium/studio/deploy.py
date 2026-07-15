"""Controller-side helper for deploying a workspace creature to a worker.

Packages a local creature directory into a hash-verified bundle, pushes it
through ``studio.deploy.push_creature_bundle``, and returns the worker-side path
used to start the deployed creature.

Usage::

    target_path = await deploy_creature_to_node(
        node_handle, Path("./my-creature/")
    )
    info = await service.add_creature(target_path, on_node="worker-1")

Each file must fit the one-shot request envelope; oversized assets require a
separate streaming transfer rather than weakening the bundle's atomicity and
validation guarantees.
"""

import base64
import hashlib
from pathlib import Path

from kohakuterrarium.laboratory.adapters.terrarium_files import (
    MAX_ONESHOT_BYTES,
)
from kohakuterrarium.laboratory.protocols import LabSender
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Base64 and request metadata expand the payload, so the raw-file ceiling must
# remain below the transport's one-shot limit.
MAX_BUNDLE_FILE_BYTES = MAX_ONESHOT_BYTES // 2  # 512 KiB

_SKIP_NAMES = {".git", "__pycache__", ".DS_Store", ".staging"}


class DeployError(RuntimeError):
    """Raised when a creature bundle cannot be safely deployed."""


def _walk_creature_files(root: Path) -> "dict[str, bytes]":
    """Collect deployable files using portable relative paths.

    Repository metadata, caches, and staging state are excluded. Files are read
    eagerly because the per-file cap bounds memory use and bundle payload size.
    """
    if not root.exists():
        raise DeployError(f"creature path does not exist: {root}")
    if not root.is_dir():
        raise DeployError(f"creature path is not a directory: {root}")
    files: dict[str, bytes] = {}
    for entry in root.rglob("*"):
        if any(part in _SKIP_NAMES for part in entry.relative_to(root).parts):
            continue
        if not entry.is_file():
            continue
        data = entry.read_bytes()
        if len(data) > MAX_BUNDLE_FILE_BYTES:
            raise DeployError(
                f"file {entry.relative_to(root)} exceeds {MAX_BUNDLE_FILE_BYTES} bytes; "
                "chunked upload not yet supported"
            )
        rel = entry.relative_to(root).as_posix()
        files[rel] = data
    if not files:
        raise DeployError(f"no files found under {root}")
    return files


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def deploy_creature_to_node(
    sender: LabSender,
    target_node: str,
    local_path: str | Path,
    *,
    name: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Push a creature bundle and return its absolute worker-side path.

    ``name`` defaults to the local directory name. Every file is accompanied by
    its SHA-256 digest so the worker can reject conflicts instead of overwriting
    divergent content. Invalid local input, transport errors, conflicts, and
    partial commits surface as :class:`DeployError`.
    """
    local = Path(local_path).expanduser().resolve()
    creature_name = name or local.name
    if not creature_name:
        raise DeployError(f"could not infer creature name from {local}")
    blobs = _walk_creature_files(local)
    wire_files = {
        rel: [_hash(data), base64.b64encode(data).decode("ascii")]
        for rel, data in blobs.items()
    }
    body = {"name": creature_name, "files": wire_files}
    response = await sender.request(
        to_node=target_node,
        namespace="studio.deploy",
        type="push_creature_bundle",
        body=body,
        timeout=timeout,
    )
    if isinstance(response, dict) and "error" in response:
        err = response["error"]
        raise DeployError(
            f"deploy to {target_node!r} failed: "
            f"{err.get('kind', 'unknown')} — {err.get('message', '')}"
        )
    conflicts = response.get("conflicts", [])
    if conflicts:
        raise DeployError(
            f"deploy to {target_node!r} aborted; hash conflicts on: {conflicts}"
        )
    # A partially committed directory is not a valid creature snapshot and must
    # never be passed to a subsequent spawn. Surface the incomplete state so the
    # caller can retry or clean it up explicitly.
    if response.get("partial"):
        raise DeployError(
            f"deploy to {target_node!r} partial; deployed={response.get('deployed', [])} "
            f"remaining={response.get('remaining', [])}: {response.get('error', '')}"
        )
    target = response.get("target_path")
    if not isinstance(target, str):
        raise DeployError("worker did not return a target_path")
    logger.info(
        "deployed creature %r to %s (%d files, %d new)",
        creature_name,
        target_node,
        len(blobs),
        len(response.get("deployed", [])),
    )
    return target


__all__ = ["DeployError", "MAX_BUNDLE_FILE_BYTES", "deploy_creature_to_node"]
