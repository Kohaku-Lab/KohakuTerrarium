"""Transfer and adopt saved session stores on Laboratory workers."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import aiofiles
from fastapi import HTTPException

from kohakuterrarium.laboratory.file_transfer import stream_write_file
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem


async def push_and_resume_member(
    *,
    host,
    request: Any,
    path: Path,
    on_node: str,
    pwd_override: str | None = None,
    workspace_overrides: dict[str, str] | None = None,
) -> tuple[str, dict, bool | None, str, list[dict[str, Any]]]:
    """Transfer one saved store and return its adopted worker identity."""
    mirror = getattr(request.app.state, "session_mirror", None)
    if mirror is not None and hasattr(mirror, "checkpoint"):
        try:
            mirror.checkpoint(normalize_session_stem(path))
        except Exception:  # pragma: no cover - defensive
            pass

    try:
        async with aiofiles.open(path, "rb") as stream:
            data = await stream.read()
    except OSError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rel = f"resume/{path.name}"
    worker_path = ""
    resume_token = uuid4().hex
    transferred = False
    resumed_sid = ""
    try:
        await stream_write_file(host, on_node, "config://", rel, data)
        transferred = True
        target_path_resp = await host.request(
            to_node=on_node,
            namespace="terrarium.files",
            type="stat",
            body={"scope": "config://", "path": rel},
            timeout=10.0,
        )
        if isinstance(target_path_resp, dict) and "error" in target_path_resp:
            error = target_path_resp["error"]
            message = error.get("message", "") if isinstance(error, dict) else ""
            raise HTTPException(
                status_code=502,
                detail=f"worker {on_node!r} failed to receive .kohakutr: {message}",
            )
        stat = (
            target_path_resp.get("stat") if isinstance(target_path_resp, dict) else None
        )
        reported_path = stat.get("path") if isinstance(stat, dict) else None
        if not isinstance(reported_path, str) or not reported_path:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"worker {on_node!r} did not report the scoped absolute "
                    "path of the transferred session"
                ),
            )
        worker_path = reported_path

        worker_response = await host.request(
            to_node=on_node,
            namespace="terrarium.session",
            type="resume",
            body={
                "scope": "config://",
                "rel": rel,
                # Older workers consume the absolute path while updated workers
                # resolve scope+rel against their own config root.
                "path": worker_path,
                "pwd_override": pwd_override,
                "workspace_overrides": workspace_overrides,
                "resume_token": resume_token,
            },
            timeout=60.0,
        )
        if not isinstance(worker_response, dict):
            raise HTTPException(
                status_code=502,
                detail=f"worker {on_node!r} returned an invalid resume response",
            )
        if "error" in worker_response:
            error = worker_response["error"]
            kind = error.get("kind") if isinstance(error, dict) else None
            status = 400 if kind in ("invalid", "not_found") else 502
            message = error.get("message", "") if isinstance(error, dict) else ""
            raise HTTPException(
                status_code=status,
                detail=f"worker {on_node!r} resume failed: {message}",
            )
        sid = worker_response.get("session_id", "")
        if not isinstance(sid, str) or not sid:
            raise HTTPException(
                status_code=502,
                detail=f"worker {on_node!r} returned no session_id",
            )
        resumed_sid = sid
        meta = worker_response.get("meta", {}) or {}
        if not isinstance(meta, dict):
            raise HTTPException(
                status_code=502,
                detail=f"worker {on_node!r} returned invalid session metadata",
            )
        raw_creatures = worker_response.get("creatures", [])
        if not isinstance(raw_creatures, list):
            raise HTTPException(
                status_code=502,
                detail=f"worker {on_node!r} returned an invalid creature roster",
            )
        worker_creatures: list[dict[str, Any]] = []
        for item in raw_creatures:
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=502,
                    detail=f"worker {on_node!r} returned an invalid creature roster",
                )
            creature_id = item.get("creature_id")
            if not isinstance(creature_id, str) or not creature_id:
                raise HTTPException(
                    status_code=502,
                    detail=f"worker {on_node!r} returned a creature without an id",
                )
            worker_creatures.append(
                {
                    "creature_id": creature_id,
                    "name": str(item.get("name") or creature_id),
                    "running": bool(item.get("running", True)),
                    "is_privileged": bool(item.get("is_privileged", False)),
                }
            )
    except Exception as exc:
        if resumed_sid:
            try:
                await host.request(
                    to_node=on_node,
                    namespace="terrarium.session",
                    type="rollback_resume",
                    body={"graph_id": resumed_sid},
                    timeout=60.0,
                )
            except Exception:
                pass
        elif transferred and worker_path:
            try:
                await host.request(
                    to_node=on_node,
                    namespace="terrarium.session",
                    type="rollback_resume",
                    body={
                        "session_path": worker_path,
                        "resume_token": resume_token,
                    },
                    timeout=30.0,
                )
            except Exception:
                pass
        elif transferred:
            try:
                await host.request(
                    to_node=on_node,
                    namespace="terrarium.files",
                    type="delete",
                    body={"scope": "config://", "path": rel},
                    timeout=30.0,
                )
            except Exception:
                pass
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=502, detail=f"lab transport error: {exc}"
        ) from exc

    worker_pwd_exists = worker_response.get("pwd_exists")
    if not isinstance(worker_pwd_exists, bool):
        worker_pwd_exists = None
    remote_session_path = str(worker_response.get("session_path") or worker_path)
    return sid, meta, worker_pwd_exists, remote_session_path, worker_creatures


__all__ = ["push_and_resume_member"]
