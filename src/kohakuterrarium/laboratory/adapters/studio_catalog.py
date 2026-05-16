"""APP extension adapter for ``studio.catalog``.

Worker-side handler exposing the worker's installed-package catalog
to the controller.  Install / uninstall / update are explicit ops —
they always target this node.  The controller's UI may aggregate
across multiple workers via :class:`CatalogAggregator`.

Wire types:

| type                  | body                   | response                   |
|-----------------------|------------------------|----------------------------|
| ``list``              | ``{}``                 | ``{packages: [pkg, ...]}`` |
| ``install``           | ``{source, editable, name?}`` | ``{installed: str}`` |
| ``uninstall``         | ``{name}``             | ``{removed: bool}``        |
"""

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
    """Per-node ``studio.catalog`` adapter.

    Install on both host and clients.  The host's adapter answers
    "what's installed here" queries; the aggregator on the controller
    fans out to every connected worker for the multi-node view.

    Mutating ops (``install`` / ``uninstall``) are intentionally
    refused on the host-side adapter (``is_host=True``).  A connected
    worker could otherwise instruct the host to ``git clone`` and
    install arbitrary code — that's remote code execution behind a
    shared token.  Host-local installs go through the operator-facing
    Studio API (Python or HTTP), not through the host's own APP
    extension.  Workers default to ``is_host=False`` and accept
    installs from the controller.
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
                # list is a cheap directory walk; OK to run on the loop.
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
        # install_package_op does git-clone / shutil.copytree / pip
        # under the hood — strictly blocking.  Off-loop so the worker
        # keeps responding to heartbeats and other APP traffic while
        # the install runs (which can take minutes for large repos).
        # NB: the wrapper kwarg is ``name``, NOT ``name_override``.
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
        # shutil.rmtree blocks too — off-loop.
        removed = await asyncio.to_thread(uninstall_package_op, name)
        return {"removed": bool(removed)}


__all__ = ["StudioCatalogAdapter"]
