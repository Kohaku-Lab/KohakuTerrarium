"""APP extension adapter for ``terrarium.session``.

Worker-side handler exposing session operations on the worker's
local :class:`SessionStore`.  Live sessions live in
``engine._session_stores`` (keyed by graph_id); on-disk sessions can
be opened on demand from the worker's session dir.

Ops shipped:

- ``history``  — paginated event read for one session/agent
- ``search``   — FTS5 / vector query
- ``stores``   — list known session ids on the worker
- ``resume``   — adopt a previously-pushed ``.kohakutr`` file at a
  worker-side path and bring its session live on the worker's engine.
  The controller drives the workflow: read the .kohakutr bytes from
  its mirror, push via ``terrarium.files.write`` to the worker's
  ``config://`` scope, then call this op with the resulting path.

Fork stays deferred: it's a file-only op the controller can perform
on its mirror directly without round-tripping.

Errors translate to the standard structured envelope.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TerrariumSessionAdapter:
    """Worker-side ``terrarium.session`` APP extension."""

    NAMESPACE = "terrarium.session"

    def __init__(self, engine: Terrarium, lab_node: LabRegistrar) -> None:
        self._engine = engine
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
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("terrarium.session handler failed: %s", msg.type)
            return {"error": {"kind": "session", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "history":
                return self._op_history(msg.body)
            case "search":
                return self._op_search(msg.body)
            case "stores":
                return self._op_stores(msg.body)
            case "resume":
                return await self._op_resume(msg.body)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported terrarium.session type: {msg.type!r}",
                    }
                }

    # ------------------------------------------------------------------
    # Ops
    # ------------------------------------------------------------------

    def _op_history(self, body: dict[str, Any]) -> dict[str, Any]:
        session_id = body.get("session_id")
        agent = body.get("agent")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id is required")
        if not isinstance(agent, str) or not agent:
            raise ValueError("agent is required")
        store = self._resolve_store(session_id)
        events = store.get_events(agent)
        since = body.get("since")
        if isinstance(since, int):
            events = [e for e in events if int(e.get("event_id", 0)) > since]
        limit = body.get("limit")
        if isinstance(limit, int) and limit > 0:
            events = events[:limit]
        return {"events": events}

    def _op_search(self, body: dict[str, Any]) -> dict[str, Any]:
        session_id = body.get("session_id")
        query = body.get("query")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id is required")
        if not isinstance(query, str) or not query:
            raise ValueError("query is required")
        store = self._resolve_store(session_id)
        k = int(body.get("k") or 10)
        hits = store.search(query, k=k)
        return {"hits": hits}

    def _op_stores(self, body: dict[str, Any]) -> dict[str, Any]:
        # Returns the session_ids of currently-attached live stores on
        # the engine.  Useful for the controller's mirror to discover
        # what sessions the worker thinks it owns.
        stores = getattr(self._engine, "_session_stores", {}) or {}
        return {"session_ids": sorted(stores.keys())}

    async def _op_resume(self, body: dict[str, Any]) -> dict[str, Any]:
        """Adopt a previously-pushed ``.kohakutr`` file on this worker.

        The controller pushes the file bytes via
        ``terrarium.files.write`` to a known worker-side path (under
        ``config://`` scope), then calls this op with that path.  This
        side just calls ``engine.adopt_session`` — which reads the
        recipe + config snapshot, runs ``add_creature``, and replays
        events from the file.  The resulting graph_id is returned so
        the controller can register it in its ``_meta`` map.

        Body shape::

            {
                "path": "<worker-side absolute path to .kohakutr>",
                "pwd_override": str | None,
                "llm_override": str | None,
            }
        """
        path = body.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("path is required")
        local = Path(path)
        if not local.exists():
            raise FileNotFoundError(f"no .kohakutr at {path!r}")
        sid = await self._engine.adopt_session(
            local,
            pwd=body.get("pwd_override"),
            llm_override=body.get("llm_override"),
        )
        store = getattr(self._engine, "_session_stores", {}).get(sid)
        meta = store.load_meta() if store is not None else {}
        return {
            "session_id": sid,
            "meta": dict(meta),
        }

    # ------------------------------------------------------------------
    # Store lookup
    # ------------------------------------------------------------------

    def _resolve_store(self, session_id: str) -> SessionStore:
        stores = getattr(self._engine, "_session_stores", {}) or {}
        store = stores.get(session_id)
        if store is None:
            raise KeyError(f"no live session store for {session_id!r}")
        return store


__all__ = ["TerrariumSessionAdapter"]
