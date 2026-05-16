"""Controller-side client for the ``terrarium.files`` namespace.

A :class:`RemoteFiles` instance is the Python surface for a single
worker node's filesystem (scope-bounded).  Studio code reaches one
through :attr:`NodeHandle.files`:

::

    files = studio.nodes["worker-1"].files
    await files.write("recipe://my-creature", "system.md", b"...")
    bytes_, h = await files.read("recipe://my-creature", "config.yaml")
    result = await files.push_bundle(
        scope="recipe://my-creature",
        files={
            "config.yaml": (sha256_hex, b"..."),
            "system.md":   (sha256_hex, b"..."),
        },
    )

All methods raise typed errors translated from the worker's wire
envelope:

- ``not_found`` → :class:`FileNotFoundError`
- ``invalid`` (malformed scope, escaped path, hash mismatch, …) →
  :class:`ValueError`
- ``denied`` → :class:`PermissionError`
- anything else → :class:`RemoteFilesError`
"""

import base64
from typing import Any

from kohakuterrarium.laboratory.protocols import LabSender


class RemoteFilesError(RuntimeError):
    """Catch-all for non-classified ``terrarium.files`` failures."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


def _maybe_raise(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and "error" in body:
        err = body["error"]
        kind = err.get("kind", "unknown")
        message = err.get("message", "")
        if kind == "not_found":
            raise FileNotFoundError(message)
        if kind == "invalid":
            raise ValueError(message)
        if kind == "denied":
            raise PermissionError(message)
        raise RemoteFilesError(kind, message)
    return body


class RemoteFiles:
    """Python wrapper around the ``terrarium.files`` APP namespace."""

    NAMESPACE = "terrarium.files"

    def __init__(
        self,
        sender: LabSender,
        target_node: str,
        *,
        request_timeout: float = 30.0,
    ) -> None:
        self._sender = sender
        self._target_node = target_node
        self._timeout = request_timeout

    @property
    def target_node(self) -> str:
        return self._target_node

    async def list(
        self,
        scope: str,
        path: str = "",
        *,
        recursive: bool = False,
    ) -> "list[dict[str, Any]]":
        body = _maybe_raise(
            await self._req(
                "list",
                {"scope": scope, "path": path, "recursive": recursive},
            )
        )
        return list(body.get("entries", []))

    async def stat(self, scope: str, path: str = "") -> dict[str, Any]:
        body = _maybe_raise(await self._req("stat", {"scope": scope, "path": path}))
        return body["stat"]

    async def read(self, scope: str, path: str) -> tuple[bytes, str]:
        body = _maybe_raise(await self._req("read", {"scope": scope, "path": path}))
        # Wire encodes bytes as base64 strings — the APP layer can't
        # carry raw bytes through kohakuvault's DataPacker.
        b64 = body.get("bytes_b64", "")
        return base64.b64decode(b64), body["sha256"]

    async def write(
        self,
        scope: str,
        path: str,
        data: bytes,
        *,
        expect_hash: str | None = None,
    ) -> tuple[int, str]:
        payload: dict[str, Any] = {
            "scope": scope,
            "path": path,
            "bytes_b64": base64.b64encode(data).decode("ascii"),
        }
        if expect_hash is not None:
            payload["expect_hash"] = expect_hash
        body = _maybe_raise(await self._req("write", payload))
        return int(body["written"]), body["sha256"]

    async def delete(self, scope: str, path: str = "") -> None:
        _maybe_raise(await self._req("delete", {"scope": scope, "path": path}))

    async def push_bundle(
        self,
        scope: str,
        files: "dict[str, tuple[str, bytes]]",
    ) -> "dict[str, list[str]]":
        """Push a content-addressable bundle.

        ``files`` is ``{rel_path: (sha256_hex, bytes)}``.  On hash
        collisions with different content the bundle aborts; the
        returned ``conflicts`` list names the offending relpaths and
        ``deployed`` is empty.  On success ``deployed`` lists the
        relpaths that were actually written (skipping idempotent
        no-ops).
        """
        wire_files = {
            rel: [h, base64.b64encode(b).decode("ascii")]
            for rel, (h, b) in files.items()
        }
        body = _maybe_raise(
            await self._req(
                "push_bundle",
                {"scope": scope, "files": wire_files},
            )
        )
        out: dict[str, Any] = {
            "deployed": list(body.get("deployed", [])),
            "conflicts": list(body.get("conflicts", [])),
        }
        # Pass through partial-deploy diagnostics so callers can decide
        # how to react (typically: retry the bundle, which is
        # idempotent — already-deployed entries hash-skip).
        for k in ("partial", "remaining", "error"):
            if k in body:
                out[k] = body[k]
        return out

    async def _req(self, type_: str, body: dict[str, Any]) -> Any:
        return await self._sender.request(
            to_node=self._target_node,
            namespace=self.NAMESPACE,
            type=type_,
            body=body,
            timeout=self._timeout,
        )


__all__ = ["RemoteFiles", "RemoteFilesError"]
