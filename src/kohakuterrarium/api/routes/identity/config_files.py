"""Identity config-files — list / read / write the user's top-level
YAML / JSON config files. CLI equivalents: ``kt config show``,
``kt config path <name>``, ``kt config edit <name>``.

Whitelisted. No arbitrary path access. Validates YAML / JSON parses
before writing. Hot-reloads the matching in-process cache when
applicable so the rest of the UI reflects the edit immediately.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.studio.identity import api_keys as _api_keys_mod
from kohakuterrarium.studio.identity import llm_profiles as _llm_profiles_mod
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
    """Whitelist of editable config files, keyed by short name.

    Resolved fresh per call so ``KT_CONFIG_DIR`` re-homing works in
    tests (otherwise we'd cache a stale path captured at import).
    """
    base = config_dir()
    return {
        "api_keys": base / "api_keys.yaml",
        "llm_profiles": base / "llm_profiles.yaml",
        "mcp_servers": base / "mcp_servers.yaml",
        "app-settings": base / "app-settings.json",
        "ui-prefs": base / "ui-prefs.yaml",
        "default-model": base / "default_model.txt",
    }


_MAX_BYTES = 1_048_576  # 1 MiB


class ConfigFileInfo(BaseModel):
    name: str
    path: str
    size: int
    mtime: float
    kind: Literal["yaml", "json", "text"]
    writable: bool
    exists: bool


class ConfigFileContent(BaseModel):
    name: str
    content: str
    sha256: str
    kind: Literal["yaml", "json", "text"]


class ConfigFileWrite(BaseModel):
    content: str
    sha256_expected: str | None = None  # optimistic concurrency


def _kind_for(path: Path) -> Literal["yaml", "json", "text"]:
    return _KIND_BY_SUFFIX.get(path.suffix.lower(), "text")


def _list_sync() -> list[ConfigFileInfo]:
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
    files = _known_files()
    if name not in files:
        raise HTTPException(404, f"Unknown config file: {name}")
    path = files[name]
    if not path.is_file():
        # Return an empty payload so the editor can render and let the
        # user create the file by saving it.
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
    """Validate parse + hot-reload caches when applicable.

    Raises ``HTTPException`` on parse failure. Reload failures are
    logged but not propagated — the file is already on disk so we
    prefer a stale-cache warning over a 500 on a successful write.
    """
    kind = _kind_for(path)
    try:
        if kind == "yaml":
            yaml.safe_load(content)
        elif kind == "json":
            json.loads(content) if content.strip() else None
    except yaml.YAMLError as e:
        raise HTTPException(400, f"YAML parse error: {e}") from e
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON parse error: {e}") from e

    try:
        if name == "llm_profiles":
            if hasattr(_llm_profiles_mod, "invalidate_cache"):
                _llm_profiles_mod.invalidate_cache()
        elif name == "mcp_servers":
            # The registry is read fresh on each ``load_servers`` call
            # already — no in-process cache to invalidate.
            pass
        elif name == "api_keys":
            if hasattr(_api_keys_mod, "invalidate_cache"):
                _api_keys_mod.invalidate_cache()
    except Exception as e:  # pragma: no cover - reload best-effort
        logger.warning("config hot-reload failed", name=name, error=str(e))


def _write_sync(name: str, body: ConfigFileWrite) -> dict:
    files = _known_files()
    if name not in files:
        raise HTTPException(404, f"Unknown config file: {name}")
    path = files[name]
    new_bytes = body.content.encode("utf-8")
    if len(new_bytes) > _MAX_BYTES:
        raise HTTPException(413, "file too large to write via editor (>1 MiB)")
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
    return await run_in_io_executor(_list_sync)


@router.get("/config-files/{name}/content", response_model=ConfigFileContent)
async def read_config_file(name: str) -> ConfigFileContent:
    return await run_in_io_executor(_read_sync, name)


@router.put("/config-files/{name}/content")
async def write_config_file(name: str, body: ConfigFileWrite):
    return await run_in_io_executor(_write_sync, name, body)


__all__ = ["router"]
