"""Private workspace identities and atomic lifecycle records."""

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from kohakuterrarium.mcp_server.config import load_config
from kohakuterrarium.utils.file_lock import FileLock


def workspace_identity(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve()))


def https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("public origin must be HTTPS without path or credentials")
    return "https://" + parsed.netloc.lower()


def write_json(path: Path, data: dict) -> None:
    """Atomic replacement; a failed write never truncates an existing record."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + 1
        while True:
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows readers/scanners can briefly deny FILE_SHARE_DELETE.
                # Keep the old complete record while waiting for their handle.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        temp.unlink(missing_ok=True)


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    workspace: str
    public_origin: str
    secret: str = Field(repr=False, pattern=r"^[A-Za-z0-9_-]{43,128}$")
    port: int = Field(default=8765, ge=1, le=65535)
    tunnel: Literal["ngrok", "external"] = "ngrok"
    ngrok_bin: str = "ngrok"
    ngrok_config: str | None = None
    tools_config: str | None = None

    @field_validator("public_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return https_origin(value)

    @property
    def url(self) -> str:
        """Credential-bearing URL, only for deliberate copy or network use."""
        return self.public_origin + "/mcp/" + self.secret


class ConnectionStore:
    """One identity and OS-backed locks per canonical workspace."""

    def __init__(self, workspace: Path, state_dir: Path | None = None):
        if not workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.workspace = workspace_identity(workspace)
        root = (state_dir or Path.home() / ".kohakuterrarium" / "mcp-serve").resolve()
        key = hashlib.sha256(self.workspace.encode()).hexdigest()[:32]
        self.directory = root / key
        self.record_path = self.directory / "connection.json"
        self.runtime_path = self.directory / "runtime.json"
        self.stop_path = self.directory / "stop.json"
        self.command_lock = FileLock(self.directory / "command.lock")
        self.instance_lock = FileLock(self.directory / "instance.lock")

    def load(self) -> Connection:
        try:
            record = Connection.model_validate(
                json.loads(self.record_path.read_text(encoding="utf-8"))
            )
        except (ValueError, OSError) as exc:
            raise ValueError(
                "Missing or invalid saved connection; configure or restore it explicitly"
            ) from exc
        if record.workspace != self.workspace:
            raise ValueError("Saved connection belongs to a different workspace")
        return record

    def configure(
        self,
        *,
        public_origin=None,
        tunnel=None,
        port=None,
        ngrok_bin=None,
        ngrok_config=None,
        tools_config=None,
        import_connection: Path | None = None,
        persist: bool = True,
    ) -> Connection:
        """Caller holds command_lock when commands may race with a running service."""
        if self.record_path.exists():
            record = self.load()
            if import_connection is not None:
                raise ValueError("Cannot import over an existing connection")
            if (
                public_origin is not None
                and https_origin(public_origin) != record.public_origin
            ):
                raise ValueError("Saved public origin cannot be silently rebound")
            data = record.model_dump()
        else:
            if import_connection is not None:
                try:
                    legacy = json.loads(import_connection.read_text(encoding="utf-8"))
                    if legacy["workspace"] != self.workspace:
                        raise ValueError(
                            "Imported connection belongs to a different workspace"
                        )
                    if (
                        public_origin is not None
                        and https_origin(public_origin) != legacy["public_origin"]
                    ):
                        raise ValueError("Imported public origin differs")
                    data = {
                        key: legacy[key]
                        for key in ("workspace", "public_origin", "secret")
                    }
                except (KeyError, TypeError) as exc:
                    raise ValueError("Invalid imported connection") from exc
            else:
                if public_origin is None:
                    raise ValueError(
                        "First start requires --public-origin and a stable HTTPS entry"
                    )
                data = {
                    "workspace": self.workspace,
                    "public_origin": https_origin(public_origin),
                    "secret": secrets.token_urlsafe(32),
                }
        updates = {
            "tunnel": tunnel,
            "port": port,
            "ngrok_bin": ngrok_bin,
            "ngrok_config": str(Path(ngrok_config).resolve()) if ngrok_config else None,
            "tools_config": str(Path(tools_config).resolve()) if tools_config else None,
        }
        data.update({key: value for key, value in updates.items() if value is not None})
        try:
            record = Connection.model_validate(data)
        except ValidationError as exc:
            raise ValueError("Invalid MCP connection settings") from exc
        if record.tools_config:
            config = load_config(Path(record.tools_config))
            if workspace_identity(config.workspace) != self.workspace:
                raise ValueError("Tool configuration belongs to a different workspace")
        if persist:
            write_json(self.record_path, record.model_dump())
        return record

    def runtime(self) -> dict:
        try:
            data = json.loads(self.runtime_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}
