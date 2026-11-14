"""Authenticated, user-scoped model upload and binary resource access."""

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import FileResponse

from kohakuterrarium.api.ext import require_user
from kohakuterrarium.utils.config_dir import config_dir

from .model_adapters import IMPORT_ADAPTERS
from .model_store import MAX_UPLOAD, current_model, import_model, model_file

router = APIRouter()
_import_lock = asyncio.Lock()


def resource_root(user=Depends(require_user)) -> Path:
    scope = f"user-{user.id}" if user is not None else "local"
    return config_dir() / "extension-data" / "live2d-pet-demo" / scope / "models"


@router.get("/models")
async def models(root: Path = Depends(resource_root)):
    return {model: current_model(root, model) for model in IMPORT_ADAPTERS}


@router.post("/models")
@router.post("/models/{model}")
async def upload(
    request: Request, model: str | None = None, root: Path = Depends(resource_root)
):
    if model is not None and model not in IMPORT_ADAPTERS:
        raise HTTPException(400, "此角色不支持导入资源。")
    if _import_lock.locked():
        raise HTTPException(409, "已有模型正在导入，请稍后重试。")
    async with _import_lock:
        with tempfile.TemporaryDirectory(prefix="kt-live2d-upload-") as directory:
            path = Path(directory) / "model.zip"
            size = 0
            with path.open("wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, "ZIP 不能超过 128 MiB。")
                    stream.write(chunk)
            # Shield the bounded CPU conversion so a disconnected client cannot
            # release the lock or delete its input while the worker still runs.
            task = asyncio.create_task(
                asyncio.to_thread(import_model, root, model, path)
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc


@router.get("/models/{model}/files/{version}/{name:path}")
async def resource(
    model: str, version: str, name: str, root: Path = Depends(resource_root)
):
    try:
        path = model_file(root, model, version, name)
    except (ValueError, OSError):
        raise HTTPException(404, "模型资源不存在。") from None
    return FileResponse(
        path,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
