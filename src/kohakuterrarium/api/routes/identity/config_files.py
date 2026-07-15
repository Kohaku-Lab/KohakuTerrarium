"""List, read, and safely write whitelisted identity configuration files.

The routes never accept arbitrary paths. Structured content is validated before
persistence, writes support optimistic concurrency, and affected in-process
caches are invalidated when possible so subsequent reads observe the edit.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.studio.identity import api_keys as _api_keys_mod
from kohakuterrarium.studio.identity import drive_settings as _drive_settings_mod
from kohakuterrarium.studio.identity import llm_profiles as _llm_profiles_mod
from kohakuterrarium.terrarium.drive.errors import (
    DriveSettingsConflictError,
    DriveValidationError,
)
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


_KIND_BY_SUFFIX: dict[str, Literal["yaml", "json", "text"]] = {
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
}


def _known_files() -> dict[str, Path]:
    """Return editable config paths keyed by their public short names.

    Paths are resolved per call so runtime config-directory overrides are not
    hidden by import-time caching.
    """
    base = config_dir()
    return {
        "api_keys": base / "api_keys.yaml",
        "llm_profiles": base / "llm_profiles.yaml",
        "mcp_servers": base / "mcp_servers.yaml",
        "app-settings": base / "app-settings.json",
        "ui-prefs": base / "ui-prefs.yaml",
        "default-model": base / "default_model.txt",
        "drive-settings": base / "drive-settings.yaml",
    }


# Bound editor payloads to avoid loading or replacing unexpectedly large files.
_MAX_BYTES = 1_048_576


class ConfigFileInfo(BaseModel):
    """Describe one whitelisted configuration file for editor discovery."""

    name: str
    path: str
    size: int
    mtime: float
    kind: Literal["yaml", "json", "text"]
    writable: bool
    exists: bool


class ConfigFileContent(BaseModel):
    """Return editable text with a revision hash for conflict detection."""

    name: str
    content: str
    sha256: str
    kind: Literal["yaml", "json", "text"]


class ConfigFileWrite(BaseModel):
    """Carry replacement text and an optional expected revision hash."""

    content: str
    sha256_expected: str | None = None


def _kind_for(path: Path) -> Literal["yaml", "json", "text"]:
    """Infer editor validation behavior from a file suffix."""
    return _KIND_BY_SUFFIX.get(path.suffix.lower(), "text")


def _list_sync() -> list[ConfigFileInfo]:
    """Collect filesystem metadata for every whitelisted config file."""
    out: list[ConfigFileInfo] = []
    for name, path in _known_files().items():
        exists = path.is_file()
        if exists:
            try:
                stat = path.stat()
                size = stat.st_size
                mtime = stat.st_mtime
            except OSError:
                size, mtime = 0, 0.0
        else:
            size, mtime = 0, 0.0
        out.append(
            ConfigFileInfo(
                name=name,
                path=str(path),
                size=size,
                mtime=mtime,
                kind=_kind_for(path),
                writable=True,
                exists=exists,
            )
        )
    return out


def _read_sync(name: str) -> ConfigFileContent:
    """Read a whitelisted UTF-8 config file and compute its revision hash."""
    files = _known_files()
    if name not in files:
        raise HTTPException(404, f"Unknown config file: {name}")
    path = files[name]
    if not path.is_file():
        # Missing known files are editable resources, not 404s; the empty hash
        # becomes the editor's revision token for creating them.
        return ConfigFileContent(
            name=name,
            content="",
            sha256=hashlib.sha256(b"").hexdigest(),
            kind=_kind_for(path),
        )
    if path.stat().st_size > _MAX_BYTES:
        raise HTTPException(413, "file too large to load in editor (>1 MiB)")
    blob = path.read_bytes()
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(415, f"non-UTF-8 content: {e}") from e
    return ConfigFileContent(
        name=name,
        content=text,
        sha256=hashlib.sha256(blob).hexdigest(),
        kind=_kind_for(path),
    )


def _validate_and_reload(name: str, path: Path, content: str) -> None:
    """Validate structured content and invalidate affected runtime caches.

    Parse failures reject the write. Cache invalidation is best-effort because a
    successfully persisted file should not be reported as a failed write merely
    because an in-process cache could not refresh.
    """
    kind = _kind_for(path)
    try:
        if kind == "yaml":
            parsed = yaml.safe_load(content)
        elif kind == "json":
            json.loads(content) if content.strip() else None
    except yaml.YAMLError as e:
        raise HTTPException(400, f"YAML parse error: {e}") from e
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON parse error: {e}") from e

    # Drive settings require schema validation in addition to YAML parsing.
    # Persistence remains separate from applying the settings to a live runtime.
    if name == "drive-settings":
        try:
            _drive_settings_mod.parse_settings(parsed)
        except DriveValidationError as e:
            raise HTTPException(400, f"drive-settings schema error: {e}") from e

    try:
        if name == "llm_profiles":
            if hasattr(_llm_profiles_mod, "invalidate_cache"):
                _llm_profiles_mod.invalidate_cache()
        elif name == "mcp_servers":
            # MCP servers are loaded fresh on demand, so no cache can become stale.
            pass
        elif name == "api_keys":
            if hasattr(_api_keys_mod, "invalidate_cache"):
                _api_keys_mod.invalidate_cache()
    except Exception as e:  # pragma: no cover - reload best-effort
        logger.warning("config hot-reload failed", name=name, error=str(e))


def _write_drive_settings(path: Path, body: ConfigFileWrite, new_bytes: bytes) -> dict:
    """Route raw editor writes through the canonical locked settings writer."""
    try:
        if body.sha256_expected is None:
            raise HTTPException(
                400,
                "drive-settings write requires sha256_expected for optimistic concurrency",
            )
        parsed = yaml.safe_load(body.content)
        # The empty-content hash represents an absent editable file, not a disk
        # revision. Translate it to the writer's explicit expect-absent contract.
        empty_revision = hashlib.sha256(b"").hexdigest()
        expected_absent = body.sha256_expected == empty_revision and not path.is_file()
        saved = _drive_settings_mod.save_settings(
            parsed,
            expected_revision=None if expected_absent else body.sha256_expected,
            expected_exists=False if expected_absent else None,
        )
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"YAML parse error: {exc}") from exc
    except DriveValidationError as exc:
        raise HTTPException(400, f"drive-settings schema error: {exc}") from exc
    except DriveSettingsConflictError as exc:
        raise HTTPException(409, "file changed externally since you opened it") from exc
    return {
        "status": "ok",
        "name": "drive-settings",
        "path": str(path),
        "sha256": saved.settings.revision,
        "size": len(new_bytes),
        "durability": saved.durability.value,
    }


def _write_sync(name: str, body: ConfigFileWrite) -> dict:
    """Validate and atomically replace a whitelisted configuration file."""
    files = _known_files()
    if name not in files:
        raise HTTPException(404, f"Unknown config file: {name}")
    path = files[name]
    new_bytes = body.content.encode("utf-8")
    if len(new_bytes) > _MAX_BYTES:
        raise HTTPException(413, "file too large to write via editor (>1 MiB)")
    if name == "drive-settings":
        return _write_drive_settings(path, body, new_bytes)
    if body.sha256_expected is not None and path.exists():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != body.sha256_expected:
            raise HTTPException(409, "file changed externally since you opened it")
    _validate_and_reload(name, path, body.content)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".kt-tmp")
    tmp.write_bytes(new_bytes)
    tmp.replace(path)
    return {
        "status": "ok",
        "name": name,
        "path": str(path),
        "sha256": hashlib.sha256(new_bytes).hexdigest(),
        "size": len(new_bytes),
    }


@router.get("/config-files", response_model=list[ConfigFileInfo])
async def list_config_files() -> list[ConfigFileInfo]:
    """List whitelisted configuration files without blocking the event loop."""
    return await run_in_io_executor(_list_sync)


@router.get("/config-files/{name}/content", response_model=ConfigFileContent)
async def read_config_file(name: str) -> ConfigFileContent:
    """Read one whitelisted configuration file through the I/O executor."""
    return await run_in_io_executor(_read_sync, name)


@router.put("/config-files/{name}/content", dependencies=[Depends(verify_admin_token)])
async def write_config_file(name: str, body: ConfigFileWrite):
    """Admin-gated write of one whitelisted configuration file."""
    return await run_in_io_executor(_write_sync, name, body)


__all__ = ["router"]
