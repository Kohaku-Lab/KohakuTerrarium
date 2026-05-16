"""Controller-side helper for deploying a workspace creature to a worker.

Walks a local creature directory, computes sha256 for each file,
calls ``studio.deploy.push_creature_bundle`` on the target node, and
returns the absolute path on the worker that
:meth:`MultiNodeTerrariumService.add_creature` should reference.

Usage::

    target_path = await deploy_creature_to_node(
        node_handle, Path("./my-creature/")
    )
    info = await service.add_creature(target_path, on_node="worker-1")

Files larger than :data:`MAX_BUNDLE_FILE_BYTES` are rejected — chunked
upload via ``terrarium.files.write_stream`` (deferred) is the path for
oversize assets.
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

# Per-file ceiling for one-shot bundle pushes.  Set slightly below the
# adapter's MAX_ONESHOT_BYTES to leave room for base64 expansion +
# envelope overhead.
MAX_BUNDLE_FILE_BYTES = MAX_ONESHOT_BYTES // 2  # 512 KiB

# Files we always skip when walking a creature directory.
_SKIP_NAMES = {".git", "__pycache__", ".DS_Store", ".staging"}


class DeployError(RuntimeError):
    """Raised when a deploy run fails (conflicts, oversize, etc.)."""


def _walk_creature_files(root: Path) -> "dict[str, bytes]":
    """Return ``{posix_rel: bytes}`` for every regular file under ``root``.

    Skips ``.git`` / ``__pycache__`` / staging directories.  Reads every
    file fully into memory — fine for creature bundles (small configs +
    prompts), explicit ``MAX_BUNDLE_FILE_BYTES`` cap below.
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
    """Push a local creature directory to ``target_node`` and return the remote path.

    Args:
        sender: lab node with a ``request()`` method — typically a
            :class:`HostEngine` in lab-host mode.
        target_node: worker's client id.
        local_path: local directory containing ``config.yaml`` +
            sibling prompt / module files.
        name: optional override for the recipe scope arg.  Defaults to
            the local directory's basename.
        timeout: request timeout for the underlying APP call.

    Returns:
        The absolute path on the worker that subsequent
        ``add_creature`` calls should reference.

    Raises:
        DeployError: if the local path is missing, oversized, or the
            worker reports a hash conflict.
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
    # A partial deploy (one or more files committed, then ``os.replace``
    # failed mid-bundle) leaves the recipe directory in a Frankenstein
    # state — the next spawn would pick up a half-written creature.
    # Refuse the partial response so the caller knows to retry or abort.
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
