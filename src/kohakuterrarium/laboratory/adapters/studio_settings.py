"""APP extension adapter for ``studio.settings`` — node-targeted Drive settings.

This is a **worker-side** adapter (installed on each worker, like
``terrarium.runtime``): it exposes the worker's own ``drive-settings.yaml`` and
live Drive runtime so the host's ``Studio.nodes[node].settings.drives`` surface
can read/validate/save/apply Drive settings *on that node's config home*
(design §8.4 Scope, §8.5 Laboratory surface).

It is deliberately NOT the credential-oriented ``studio.identity`` adapter, whose
host→worker credential direction has different security semantics. Node-targeted
settings mutation is gated by the operator/admin authorization at the host API;
the worker trusts its authenticated Lab peer and never reads an actor from the
request payload.

Because the worker process sets ``KT_CONFIG_DIR`` at boot, the ``drive_settings``
module functions here resolve the *worker's* config home, and ``apply_runtime``
targets the *worker's* live engine — the host never edits the worker file as a
proxy.
"""

import asyncio
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory._internal.protocol import HOST_NODE_ID
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.studio.identity import drive_settings as _drive_settings
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.terrarium.wire import pack_drive_error
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class StudioSettingsAdapter:
    """Worker-side ``studio.settings`` APP extension for Drive settings.

    Args:
        engine: the worker's live :class:`Terrarium` (for live apply + status).
        lab_node: the Lab client this worker runs as.
        node_id: this node's id (defaults to ``lab_node.client_id`` / ``_host``).
    """

    NAMESPACE = "studio.settings"

    def __init__(
        self,
        engine: Terrarium,
        lab_node: LabRegistrar,
        *,
        node_id: str | None = None,
    ) -> None:
        self._engine = engine
        self._node = lab_node
        # Resolve the node id LIVE per request: on a worker ``client_id`` is only
        # assigned after ``client.start()``, so a value captured at construction
        # would stick at ``_host`` and mislabel the node in status responses.
        self._explicit_node_id = node_id
        self._drive_service: LocalTerrariumService | None = None
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    @property
    def node_id(self) -> str:
        return (
            self._explicit_node_id or getattr(self._node, "client_id", None) or "_host"
        )

    def detach(self) -> None:
        self._node.unregister_app_extension(self.NAMESPACE)
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    def _local_service(self) -> LocalTerrariumService:
        if self._drive_service is None:
            self._drive_service = LocalTerrariumService(
                self._engine, node_id=self.node_id
            )
        return self._drive_service

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        # Node-targeted settings mutation is host-only: the host derives operator
        # authority from its request context and issues from ``_host``. A peer
        # worker is never trusted here (R1-04); reject before touching disk.
        if msg.sender_node != HOST_NODE_ID:
            return {
                "error": {
                    "kind": "forbidden",
                    "message": (
                        f"studio.settings verb {msg.type!r} refused from non-host "
                        f"origin {msg.sender_node!r}"
                    ),
                }
            }
        try:
            return await self._handle(msg)
        except DriveError as exc:
            # Typed Drive/settings errors (validation, optimistic-concurrency
            # conflict) survive the wire as their subtype.
            return {"error": pack_drive_error(exc)}
        except ValueError as exc:
            return {"error": {"kind": "invalid", "message": str(exc)}}
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("studio.settings handler failed: %s", msg.type)
            return {"error": {"kind": "settings", "message": str(exc)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        body = msg.body or {}
        match msg.type:
            case "drive_status":
                return {"status": _drive_settings.settings_status(self.node_id)}

            case "drive_get":
                settings = _drive_settings.load_settings()
                return {
                    "settings": _drive_settings.settings_to_dict(settings),
                    "revision": settings.revision,
                }

            case "drive_validate":
                _drive_settings.parse_settings(body.get("settings"))
                return {"ok": True}

            case "drive_save":
                saved = await asyncio.to_thread(
                    _drive_settings.save_settings,
                    body.get("settings"),
                    expected_revision=body.get("expected_revision"),
                    expected_exists=body.get("expected_exists"),
                )
                return {
                    "ok": True,
                    "revision": saved.settings.revision,
                    "durability": saved.durability.value,
                }

            case "drive_apply":
                return {
                    "result": _drive_settings.apply_runtime(
                        self._engine, node=self.node_id
                    )
                }

            case "drive_runtime_status":
                status = await self._local_service().drive_runtime_status()
                return {"runtime_status": status.to_dict()}

            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported studio.settings type: {msg.type!r}",
                    }
                }


__all__ = ["StudioSettingsAdapter"]
