"""APP extension adapter for ``studio.deploy``.

Worker-side handler for the headline multi-node workflow: controller
authors a creature in its local workspace, then asks a worker to
spawn it.  The controller can't pass the local path directly (the
worker's filesystem has different file layout), so the bundle is
pushed first via this adapter and the resolved remote path is
returned for the subsequent :meth:`add_creature` call.

Single op for now:

- ``push_creature_bundle {name, files}`` — install a creature bundle
  into ``recipe://<name>`` on this worker and return the absolute
  target path the controller should pass to ``add_creature``.

Reuses the bundle algorithm from
:mod:`kohakuterrarium.laboratory.adapters.terrarium_files`, just
adding the convention "creature name → ``recipe://<name>`` scope".
"""

import re
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.laboratory.adapters.file_scopes import (
    ScopeError,
    resolve_scope_root,
)
from kohakuterrarium.laboratory.adapters.terrarium_files import (
    TerrariumFilesAdapter,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Names are used as a directory under ``~/.kohakuterrarium/recipes/`` on
# the worker.  Be strict: alphanumerics, underscore, dot, hyphen.  Don't
# allow a leading dot (no hidden-dirs), no path separators, no drive
# prefixes, no parent-traversal segments.
_NAME_RE = re.compile(r"^(?!\.)[A-Za-z0-9_.-]+$")


class StudioDeployAdapter:
    """Worker-side ``studio.deploy`` APP extension.

    Composes over a :class:`TerrariumFilesAdapter` — either an
    existing one passed in or a fresh internal instance.
    """

    NAMESPACE = "studio.deploy"

    def __init__(
        self,
        engine: Terrarium,
        lab_node: LabRegistrar,
        *,
        files_adapter: TerrariumFilesAdapter | None = None,
    ) -> None:
        self._engine = engine
        self._node = lab_node
        self._files = files_adapter or TerrariumFilesAdapter(engine, lab_node)
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info(
            "lab adapter registered",
            namespace=self.NAMESPACE,
            shared_files_adapter=files_adapter is not None,
        )

    def detach(self) -> None:
        self._node.unregister_app_extension(self.NAMESPACE)
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except ScopeError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("studio.deploy handler failed: %s", msg.type)
            return {"error": {"kind": "deploy", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "push_creature_bundle":
                return await self._op_push_creature_bundle(msg.body)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported studio.deploy type: {msg.type!r}",
                    }
                }

    async def _op_push_creature_bundle(self, body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise ScopeError("push_creature_bundle requires a string 'name'")
        if not _NAME_RE.match(name):
            raise ScopeError(
                f"creature name must match [A-Za-z0-9_.-]+ and not start "
                f"with '.'; got {name!r}"
            )
        files = body.get("files")
        if not isinstance(files, dict):
            raise ScopeError("push_creature_bundle requires 'files' dict")
        scope = f"recipe://{name}"
        bundle_result = await self._files._op_push_bundle(
            {"scope": scope, "files": files}
        )
        target_path = resolve_scope_root(scope, self._engine)
        # Forward the full bundle result so partial-failure state
        # (``partial`` / ``remaining`` / ``error``) reaches the
        # controller.  Without this the caller sees ``conflicts: []``
        # and assumes a clean deploy even though half the files never
        # made it onto disk.
        response: dict[str, Any] = {
            "target_path": str(target_path),
            "deployed": bundle_result.get("deployed", []),
            "conflicts": bundle_result.get("conflicts", []),
        }
        for k in ("partial", "remaining", "error"):
            if k in bundle_result:
                response[k] = bundle_result[k]
        return response


__all__ = ["StudioDeployAdapter"]
