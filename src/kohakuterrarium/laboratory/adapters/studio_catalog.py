"""Expose each node's installed package catalog through Studio."""

import asyncio
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.studio.catalog.packages import (
    install_package_op,
    list_installed_packages,
    uninstall_package_op,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class StudioCatalogAdapter:
    """Serve per-node catalog queries and worker package mutations.

    Host-side mutations are refused because allowing workers to install code on
    the host would turn the shared cluster credential into remote code execution.
    """

    NAMESPACE = "studio.catalog"

    def __init__(self, lab_node: LabRegistrar, *, is_host: bool = False) -> None:
        self._node = lab_node
        self._is_host = is_host
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info(
            "lab adapter registered",
            namespace=self.NAMESPACE,
            is_host=is_host,
        )

    def detach(self) -> None:
        self._node.unregister_app_extension(self.NAMESPACE)
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except PermissionError as e:
            return {"error": {"kind": "denied", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("studio.catalog handler failed: %s", msg.type)
            return {"error": {"kind": "catalog", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "list":
                return {"packages": list_installed_packages()}
            case "install":
                return await self._op_install(msg.body)
            case "uninstall":
                return await self._op_uninstall(msg.body)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported studio.catalog type: {msg.type!r}",
                    }
                }

    async def _op_install(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._is_host:
            raise PermissionError(
                "install via studio.catalog is disabled on the host adapter; "
                "the host's local installs go through the operator API"
            )
        source = body.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError("source is required")
        editable = bool(body.get("editable", False))
        name_override = body.get("name")
        if name_override is not None and not isinstance(name_override, str):
            raise ValueError("name must be a string if provided")
        # Installation performs blocking filesystem, Git, and package-manager work.
        installed = await asyncio.to_thread(
            install_package_op,
            source,
            editable=editable,
            name=name_override,
        )
        return {"installed": installed}

    async def _op_uninstall(self, body: dict[str, Any]) -> dict[str, Any]:
        if self._is_host:
            raise PermissionError(
                "uninstall via studio.catalog is disabled on the host adapter"
            )
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("name is required")
        # Recursive package removal is blocking filesystem work.
        removed = await asyncio.to_thread(uninstall_package_op, name)
        return {"removed": bool(removed)}


__all__ = ["StudioCatalogAdapter"]
