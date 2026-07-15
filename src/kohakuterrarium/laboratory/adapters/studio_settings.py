"""Expose node-local Drive settings and runtime state to the host Studio."""

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
    """Serve a worker's Drive settings through ``studio.settings``."""

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
        # Worker IDs are assigned at client start, so resolve implicit IDs lazily.
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
        # Only the authenticated host may authorize node-local settings access.
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
            # Preserve validation and concurrency subtypes across the wire.
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
