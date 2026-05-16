"""APP extension adapter for ``studio.identity``.

Host-side handler that lets workers fetch the controller's identity
state: API keys, LLM profile bodies, MCP server configs.  Clients
query the host; the host responds with the requested record (or
``not_found``).

This is the foundation for the hybrid identity model
(``management-wiring.md``).  The host is the source of truth; workers
may cache via :class:`IdentityCache` for hot-path performance.
Invalidation broadcasts and push-at-spawn are layered on top in
follow-up units.

Wire types:

| type              | body              | response                           |
|-------------------|-------------------|------------------------------------|
| ``get_api_key``   | ``{provider}``    | ``{key}``                          |
| ``get_profile``   | ``{name}``        | ``{profile}``                      |
| ``list_profiles`` | ``{}``            | ``{profiles}``                     |
| ``get_mcp_server``| ``{name}``        | ``{server}``                       |
| ``list_mcp_servers`` | ``{}``         | ``{servers}``                      |

Errors translate to the standard envelope (``not_found`` /
``invalid`` / ``identity``).  An empty API key from the store is
treated as ``not_found`` — workers calling ``get_api_key`` get a
clear signal rather than a silently-empty string.
"""

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
    get_status as codex_get_status,
    login_async as codex_login_async,
)
from kohakuterrarium.studio.identity.llm_profiles import list_profiles_payload
from kohakuterrarium.studio.identity.mcp_servers import load_servers
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class StudioIdentityAdapter:
    """Host-side ``studio.identity`` APP extension.

    Install once on the host; workers issue APP requests against
    ``studio.identity`` and the handler reads from the controller's
    local identity files (``~/.kohakuterrarium/api_keys.json`` etc.).
    """

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
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported studio.identity type: {msg.type!r}",
                    }
                }

    # ------------------------------------------------------------------
    # Ops
    # ------------------------------------------------------------------

    def _op_get_api_key(self, body: dict[str, Any]) -> dict[str, Any]:
        provider = body.get("provider")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider is required")
        key = get_existing_key(provider)
        if not key:
            raise KeyError(f"no API key configured for provider {provider!r}")
        return {"key": key}

    def _op_get_codex_token(self) -> dict[str, Any]:
        """Return the host's stored Codex OAuth tokens (or 404)."""
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
        """Run the Codex OAuth flow on THIS node.

        On a worker, this opens a browser on the worker's machine (or
        prints a device-code URL) so the user can authenticate the
        worker process directly.  Process-local tokens are saved to the
        worker's ``<config_dir>/codex-auth.json``.  This is the only
        sound way to use Codex from a worker — the host's token is
        process-bound and cannot be reused remotely.
        """
        result = await codex_login_async()
        return result

    def _op_get_mcp_server(self, body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("name is required")
        for server in load_servers():
            if server.get("name") == name:
                return {"server": server}
        raise KeyError(f"no MCP server named {name!r}")


__all__ = ["StudioIdentityAdapter"]
