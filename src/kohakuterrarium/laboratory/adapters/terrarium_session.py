"""Expose a worker's live session stores through ``terrarium.session``.

The adapter supports history, search, store discovery, and adoption of a
``.kohakutr`` file that the controller has already copied to the worker.
"""

import os
import time
from pathlib import Path
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.file_scopes import resolve_in_scope
from kohakuterrarium.laboratory.protocols import LabRegistrar
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_RESUME_TOKEN_TTL_SECONDS = 300.0
_MAX_RESUME_TOKENS = 256


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


def _transfer_path(engine: Terrarium, raw_path: object) -> Path:
    """Resolve a controller-uploaded session path within config/resume."""
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("session_path is required")
    path = Path(raw_path).expanduser().resolve(strict=False)
    transfer_root = resolve_in_scope("config://", "resume", engine)
    try:
        path.relative_to(transfer_root)
    except ValueError as exc:
        raise ValueError(
            "session_path must be inside the config resume transfer directory"
        ) from exc
    if path.suffix != ".kohakutr":
        raise ValueError("session_path must identify a .kohakutr transfer")
    return path


class TerrariumSessionAdapter:
    """Worker-side ``terrarium.session`` APP extension."""

    NAMESPACE = "terrarium.session"

    def __init__(self, engine: Terrarium, lab_node: LabRegistrar) -> None:
        self._engine = engine
        self._node = lab_node
        self._resume_tokens: dict[str, tuple[str, str, float]] = {}
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
            case "set_lifecycle":
                return self._op_set_lifecycle(msg.body)
            case "rollback_resume":
                return await self._op_rollback_resume(msg.body)
            case "delete_transfer":
                return self._op_delete_transfer(msg.body)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported terrarium.session type: {msg.type!r}",
                    }
                }

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
        # Only attached stores are authoritative for sessions owned by this worker.
        stores = getattr(self._engine, "_session_stores", {}) or {}
        session_id = str(body.get("session_id") or "")
        details = []
        for graph_id, store in stores.items():
            if session_id and session_id not in {str(graph_id), str(store.session_id)}:
                continue
            details.append(
                {
                    "session_id": str(graph_id),
                    "path": str(store.path),
                    "conversation_id": str(store.meta.get("conversation_id") or ""),
                }
            )
        result: dict[str, Any] = {"session_ids": sorted(stores.keys())}
        if session_id:
            result["stores"] = details
        return result

    def _op_set_lifecycle(self, body: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(body.get("session_path") or body.get("path") or ""))
        if not path.is_file():
            raise ValueError("set_lifecycle requires an existing session_path")
        is_open = bool(body.get("conversation_open"))
        status = str(body.get("status") or ("running" if is_open else "completed"))
        stores = getattr(self._engine, "_session_stores", {}) or {}
        store = next(
            (item for item in stores.values() if Path(item.path) == path),
            None,
        )
        owns_store = store is None
        if store is None:
            store = SessionStore(path)
        try:
            store.set_conversation_open(is_open)
            store.update_status(status)
            store.checkpoint()
        finally:
            if owns_store:
                store.close(update_status=False)
        return {"ok": True, "session_path": str(path)}

    def _op_delete_transfer(self, body: dict[str, Any]) -> dict[str, Any]:
        path = _transfer_path(self._engine, body.get("session_path"))
        stores = getattr(self._engine, "_session_stores", {}) or {}
        if any(_path_key(store.path) == _path_key(path) for store in stores.values()):
            raise ValueError("delete_transfer refuses an active session store")
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            candidate.unlink(missing_ok=True)
        return {"ok": True, "session_path": str(path)}

    def _remember_resume_token(self, token: str, graph_id: str, path: Path) -> None:
        now = time.monotonic()
        self._resume_tokens = {
            key: value for key, value in self._resume_tokens.items() if value[2] > now
        }
        while len(self._resume_tokens) >= _MAX_RESUME_TOKENS:
            self._resume_tokens.pop(next(iter(self._resume_tokens)))
        self._resume_tokens[token] = (
            graph_id,
            _path_key(path),
            now + _RESUME_TOKEN_TTL_SECONDS,
        )

    def _resolve_resume_token(self, token: str, path: Path) -> str:
        claim = self._resume_tokens.get(token)
        if claim is None or claim[2] <= time.monotonic():
            self._resume_tokens.pop(token, None)
            return ""
        if claim[1] != _path_key(path):
            raise ValueError("resume_token does not match session_path")
        return claim[0]

    def _forget_graph_tokens(self, graph_id: str) -> None:
        for token, claim in list(self._resume_tokens.items()):
            if claim[0] == graph_id:
                self._resume_tokens.pop(token, None)

    async def _op_rollback_resume(self, body: dict[str, Any]) -> dict[str, Any]:
        graph_id = str(body.get("graph_id") or "")
        requested_path = str(body.get("session_path") or "")
        if not graph_id and not requested_path:
            raise ValueError("rollback_resume requires graph_id or session_path")
        stores = getattr(self._engine, "_session_stores", {}) or {}
        if not graph_id:
            token = body.get("resume_token")
            if not isinstance(token, str) or not token:
                raise ValueError("path rollback requires resume_token")
            transfer_path = _transfer_path(self._engine, requested_path)
            requested_path = str(transfer_path)
            graph_id = self._resolve_resume_token(token, transfer_path)
            if not graph_id:
                return self._op_delete_transfer({"session_path": requested_path})
        store = stores.get(graph_id)
        session_path = (
            Path(store.path)
            if store is not None
            else (Path(requested_path) if requested_path else None)
        )
        if store is not None and stores.get(graph_id) is store:
            # Detach before removals so topology changes cannot split the
            # just-adopted store into fresh persisted sessions.
            stores.pop(graph_id, None)
            owned_sessions = getattr(self._engine, "_owned_sessions", None)
            if isinstance(owned_sessions, set):
                owned_sessions.discard(graph_id)
        creature_ids = [
            creature.creature_id
            for creature in self._engine.list_creatures()
            if creature.graph_id == graph_id
        ]
        for creature_id in reversed(creature_ids):
            await self._engine.remove_creature(creature_id)
        if store is not None:
            try:
                store.close(update_status=False)
            except Exception:
                pass
        self._forget_graph_tokens(graph_id)
        if session_path is not None:
            for candidate in (
                session_path,
                Path(f"{session_path}-wal"),
                Path(f"{session_path}-shm"),
            ):
                candidate.unlink(missing_ok=True)
        return {"ok": True, "removed": creature_ids}

    async def _op_resume(self, body: dict[str, Any]) -> dict[str, Any]:
        """Adopt a session file already present on the worker."""
        path = body.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("path is required")
        resume_token = body.get("resume_token")
        if resume_token is not None and (
            not isinstance(resume_token, str)
            or not resume_token
            or len(resume_token) > 128
        ):
            raise ValueError(
                "resume_token must be a non-empty string of at most 128 chars"
            )
        local = Path(path)
        if not local.exists():
            raise FileNotFoundError(f"no .kohakutr at {path!r}")
        sid = await self._engine.adopt_session(
            local,
            pwd=body.get("pwd_override"),
            llm=body.get("llm"),
        )
        if isinstance(resume_token, str):
            self._remember_resume_token(resume_token, sid, local)
        store = getattr(self._engine, "_session_stores", {}).get(sid)
        meta = store.load_meta() if store is not None else {}
        creatures = [
            {
                "creature_id": str(creature.creature_id),
                "name": str(getattr(creature, "name", creature.creature_id)),
                "running": bool(getattr(creature, "is_running", True)),
                "is_privileged": bool(getattr(creature, "is_privileged", False)),
            }
            for creature in self._engine.list_creatures()
            if getattr(creature, "graph_id", None) == sid
        ]
        # Path validity must be evaluated here; the controller cannot stat the
        # worker's filesystem.
        saved_pwd = str(meta.get("pwd", "") or "")
        return {
            "session_id": sid,
            "session_path": str(path),
            "meta": dict(meta),
            "creatures": creatures,
            "pwd_exists": (not saved_pwd) or os.path.isdir(saved_pwd),
        }

    def _resolve_store(self, session_id: str) -> SessionStore:
        stores = getattr(self._engine, "_session_stores", {}) or {}
        store = stores.get(session_id)
        if store is None:
            raise KeyError(f"no live session store for {session_id!r}")
        return store


__all__ = ["TerrariumSessionAdapter"]
