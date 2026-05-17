"""Catalog packages — list / install / uninstall / update / browse / edit.

Replaces the legacy ``api.routes.registry`` module: every reader
projects from the canonical
``studio.catalog.packages_scan.scan_catalog`` and every operation
delegates to ``studio.catalog.packages``.

Mounted twice by ``api/app.py``:
- ``/api/catalog/packages`` (new canonical prefix)
- ``/api/registry``         (legacy URL preservation; same router)
"""

import hashlib
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.packages.locations import PACKAGES_DIR
from kohakuterrarium.studio.catalog.packages import (
    install_package_op,
    uninstall_package_op,
    update_all_packages_op,
    update_package_op,
)
from kohakuterrarium.studio.catalog.packages_scan import scan_catalog
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class InstallRequest(BaseModel):
    """Request body for installing a package."""

    url: str
    name: str | None = None


class UninstallRequest(BaseModel):
    """Request body for uninstalling a package."""

    name: str


class FileEntry(BaseModel):
    path: str  # relative to the package root
    size: int
    mtime: float
    is_dir: bool


class FileContent(BaseModel):
    content: str
    sha256: str
    encoding: Literal["utf-8", "binary"]


class FileWrite(BaseModel):
    content: str
    sha256_expected: str | None = None  # optimistic concurrency


_MAX_FILE_BYTES = 1_048_576  # 1 MiB
_TEXT_SUFFIXES = {
    ".yaml",
    ".yml",
    ".json",
    ".md",
    ".markdown",
    ".txt",
    ".py",
    ".toml",
    ".ini",
    ".cfg",
    ".jinja",
    ".jinja2",
    ".j2",
    ".sh",
    ".html",
    ".css",
    ".js",
}


@router.get("")
async def list_local():
    """List all locally available creature and terrarium configs with details.

    Off-loaded to the shared I/O executor — ``scan_catalog`` walks the
    packages dir and reads metadata for every installed entry.
    """
    entries = await run_in_io_executor(scan_catalog)
    return [entry.as_registry_dict() for entry in entries]


@router.post("/install")
async def install(req: InstallRequest):
    """Install a package from a git URL."""
    try:
        name = await run_in_io_executor(
            install_package_op, source=req.url, name=req.name
        )
        return {"status": "installed", "name": name}
    except Exception as e:
        logger.error("Install failed", url=req.url, error=str(e))
        raise HTTPException(400, f"Install failed: {e}")


@router.post("/uninstall")
async def uninstall(req: UninstallRequest):
    """Uninstall a package by name."""
    removed = await run_in_io_executor(uninstall_package_op, req.name)
    if not removed:
        raise HTTPException(404, f"Package not found: {req.name}")
    return {"status": "uninstalled", "name": req.name}


@router.post("/{name}/update")
async def update_one(name: str):
    """Update a single git-backed installed package.

    409 for non-git / editable packages (skipped, not an error); 500
    for actual update failures.
    """
    rc, msg = await run_in_io_executor(update_package_op, name)
    if rc == 0:
        return {"status": "ok", "name": name, "message": msg}
    raise HTTPException(500, msg)


@router.post("/update-all")
async def update_all():
    """Update every git-backed installed package; returns a summary."""
    rc, messages, updated, skipped = await run_in_io_executor(update_all_packages_op)
    return {
        "ok": rc == 0,
        "updated": updated,
        "skipped": skipped,
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Package file browsing + editing
# ---------------------------------------------------------------------------


def _resolve_pkg_root(name: str) -> Path:
    """Find ``<PACKAGES_DIR>/<name>`` or raise 404."""
    root = PACKAGES_DIR / name
    if not root.is_dir():
        raise HTTPException(404, f"Package not found: {name}")
    return root


def _resolve_pkg_file(name: str, rel_path: str) -> Path:
    """Resolve ``rel_path`` against the package root, blocking traversal."""
    root = _resolve_pkg_root(name)
    if not rel_path:
        raise HTTPException(400, "path is required")
    root_resolved = root.resolve()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as e:
        raise HTTPException(403, "path escapes package root") from e
    return candidate


def _list_files_sync(name: str) -> list[FileEntry]:
    root = _resolve_pkg_root(name)
    out: list[FileEntry] = []
    for p in sorted(root.rglob("*")):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            # Skip dot-dirs (.git, .venv, __pycache__-adjacent)
            continue
        if any(part == "__pycache__" for part in p.relative_to(root).parts):
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append(
            FileEntry(
                path=str(p.relative_to(root).as_posix()),
                size=stat.st_size,
                mtime=stat.st_mtime,
                is_dir=p.is_dir(),
            )
        )
    return out


def _read_file_sync(name: str, rel_path: str) -> FileContent:
    target = _resolve_pkg_file(name, rel_path)
    if not target.is_file():
        raise HTTPException(404, f"File not found: {rel_path}")
    if target.stat().st_size > _MAX_FILE_BYTES:
        raise HTTPException(413, "file too large to load in editor (>1 MiB)")
    blob = target.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    if target.suffix.lower() in _TEXT_SUFFIXES:
        try:
            return FileContent(
                content=blob.decode("utf-8"), sha256=sha, encoding="utf-8"
            )
        except UnicodeDecodeError:
            pass
    # Binary fallback — surface as opaque length info, not the bytes.
    raise HTTPException(415, "binary files cannot be read via this endpoint")


def _write_file_sync(name: str, rel_path: str, body: FileWrite) -> dict:
    target = _resolve_pkg_file(name, rel_path)
    if target.is_dir():
        raise HTTPException(400, "target is a directory")
    new_bytes = body.content.encode("utf-8")
    if len(new_bytes) > _MAX_FILE_BYTES:
        raise HTTPException(413, "file too large to write via editor (>1 MiB)")
    if body.sha256_expected is not None and target.exists():
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != body.sha256_expected:
            raise HTTPException(409, "file changed externally since you opened it")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".kt-tmp")
    tmp.write_bytes(new_bytes)
    tmp.replace(target)
    return {
        "status": "ok",
        "path": rel_path,
        "sha256": hashlib.sha256(new_bytes).hexdigest(),
        "size": len(new_bytes),
    }


@router.get("/{name}/files", response_model=list[FileEntry])
async def list_package_files(name: str) -> list[FileEntry]:
    return await run_in_io_executor(_list_files_sync, name)


@router.get("/{name}/files/{path:path}", response_model=FileContent)
async def read_package_file(name: str, path: str) -> FileContent:
    return await run_in_io_executor(_read_file_sync, name, path)


@router.put("/{name}/files/{path:path}")
async def write_package_file(name: str, path: str, body: FileWrite):
    return await run_in_io_executor(_write_file_sync, name, path, body)
