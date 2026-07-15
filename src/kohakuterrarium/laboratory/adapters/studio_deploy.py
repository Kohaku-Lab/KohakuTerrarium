"""APP extension adapter for ``studio.deploy``.

Deploy creature bundles from a controller to a worker.

Controller-local paths are meaningless on a worker, so the adapter installs
bundle files under ``recipe://<name>`` and returns the worker's resolved path
for a subsequent ``add_creature`` call. Bundle installation is delegated to
:class:`TerrariumFilesAdapter`.
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


# Names become worker-side recipe directory names. Restricting the alphabet and
# rejecting a leading dot prevents hidden directories and path traversal.
_NAME_RE = re.compile(r"^(?!\.)[A-Za-z0-9_.-]+$")


class StudioDeployAdapter:
    """Handle worker-side ``studio.deploy`` application messages."""

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
        # Preserve partial-failure fields so the controller cannot mistake an
        # incomplete write for a conflict-free deployment.
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
