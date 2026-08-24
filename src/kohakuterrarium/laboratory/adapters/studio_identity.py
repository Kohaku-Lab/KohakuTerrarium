"""Expose host identity records and Codex operations to laboratory workers."""

from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.studio.identity.api_keys import (
    get_existing_key,
    list_keys_payload,
    remove_key,
    set_key,
)
from kohakuterrarium.studio.identity.codex_oauth import (
    consume_reset_credit_async as codex_consume_reset_credit,
    get_status as codex_get_status,
    get_usage_async as codex_get_usage,
    login_async as codex_login_async,
)
from kohakuterrarium.studio.identity.llm_profiles import list_profiles_payload
from kohakuterrarium.studio.identity.grok_subscription import (
    get_status as grok_get_status,
)
from kohakuterrarium.studio.identity.mcp_servers import load_servers
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class StudioIdentityAdapter:
    """Serve controller-local identity state through ``studio.identity``."""

    NAMESPACE = "studio.identity"

    def __init__(self, lab_node: LabRegistrar) -> None:
        self._node = lab_node
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    def detach(self) -> None:
        self._node.unregister_app_extension(self.NAMESPACE)
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except (LookupError,) as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("studio.identity handler failed: %s", msg.type)
            return {"error": {"kind": "identity", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "get_api_key":
                return self._op_get_api_key(msg.body)
            case "get_profile":
                return self._op_get_profile(msg.body)
            case "list_profiles":
                return {"profiles": list_profiles_payload()}
            case "get_mcp_server":
                return self._op_get_mcp_server(msg.body)
            case "list_mcp_servers":
                return {"servers": load_servers()}
            case "get_codex_token":
                return self._op_get_codex_token()
            case "list_keys":
                return {"providers": list_keys_payload()}
            case "save_key":
                return self._op_save_key(msg.body)
            case "remove_key":
                return self._op_remove_key(msg.body)
            case "codex_login":
                return await self._op_codex_login()
            case "codex_status":
                return codex_get_status()
            case "grok_status":
                return grok_get_status()
            case "codex_usage":
                return await codex_get_usage()
            case "codex_reset_consume":
                return await self._op_codex_reset_consume(msg.body)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported studio.identity type: {msg.type!r}",
                    }
                }

    def _op_get_api_key(self, body: dict[str, Any]) -> dict[str, Any]:
        provider = body.get("provider")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider is required")
        key = get_existing_key(provider)
        if not key:
            raise KeyError(f"no API key configured for provider {provider!r}")
        return {"key": key}

    def _op_get_codex_token(self) -> dict[str, Any]:
        """Return stored Codex OAuth tokens or report them as missing."""
        tokens = CodexTokens.load()
        if tokens is None or not tokens.access_token:
            raise KeyError("no Codex tokens configured on the host")
        return {
            "tokens": {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
                "id_token": tokens.id_token,
                "account_id": tokens.account_id,
            }
        }

    def _op_get_profile(self, body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("name is required")
        for profile in list_profiles_payload():
            if profile.get("name") == name:
                return {"profile": profile}
        raise KeyError(f"no LLM profile named {name!r}")

    def _op_save_key(self, body: dict[str, Any]) -> dict[str, Any]:
        provider = body.get("provider")
        key = body.get("key")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider is required")
        if not isinstance(key, str) or not key:
            raise ValueError("key is required")
        set_key(provider, key)
        return {"status": "saved", "provider": provider}

    def _op_remove_key(self, body: dict[str, Any]) -> dict[str, Any]:
        provider = body.get("provider")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider is required")
        remove_key(provider)
        return {"status": "removed", "provider": provider}

    async def _op_codex_login(self) -> dict[str, Any]:
        """Authenticate Codex on this node because its tokens are process-bound."""
        result = await codex_login_async()
        return result

    async def _op_codex_reset_consume(self, body: dict[str, Any]) -> dict[str, Any]:
        """Redeem a reset credit using this node's process-bound Codex account."""
        return await codex_consume_reset_credit(
            idempotency_key=body.get("idempotency_key") or None,
            credit_id=body.get("credit_id") or None,
        )

    def _op_get_mcp_server(self, body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("name is required")
        for server in load_servers():
            if server.get("name") == name:
                return {"server": server}
        raise KeyError(f"no MCP server named {name!r}")


__all__ = ["StudioIdentityAdapter"]
