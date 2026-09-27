"""Private workspace identities and atomic lifecycle records."""

import hashlib
import json
import os
import secrets
import shutil
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
    parsed.port  # Reject malformed or out-of-range ports before saving.
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

    def summary(self) -> dict:
        """Public settings only; diagnostics must never serialize the secret."""
        return self.model_dump(exclude={"secret", "version"})


def validate_dependencies(record: Connection) -> None:
    """Validate local dependencies, without starting processes or making requests."""
    if record.tools_config:
        config = load_config(Path(record.tools_config))
        if workspace_identity(config.workspace) != record.workspace:
            raise ValueError("Tool configuration belongs to a different workspace")
    if record.tunnel == "ngrok":
        if not shutil.which(record.ngrok_bin):
            raise ValueError(
                "ngrok executable not found; configure --ngrok-bin in setup"
            )
        if record.ngrok_config:
            try:
                with Path(record.ngrok_config).open("rb") as stream:
                    stream.read(1)
            except OSError:
                raise ValueError(
                    "ngrok configuration must be an existing readable file"
                ) from None


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
        self.active_path = self.directory / "active.json"
        self.command_lock = FileLock(self.directory / "command.lock")
        self.instance_lock = FileLock(self.directory / "instance.lock")

    def load(self) -> Connection:
        record, _ = self.read_configuration()
        if record is None:
            raise ValueError("No saved connection; run kt mcp-serve setup first")
        return record

    @property
    def server_arguments(self):
        return [
            "--workspace",
            self.workspace,
            "--state-dir",
            str(self.directory.parent),
        ]

    @property
    def process_directory(self):
        return self.workspace

    def owns(self, record):
        return record.workspace == self.workspace

    def validate(self, record):
        validate_dependencies(record)

    def configuration_summary(self, record):
        return record.summary()

    def active_summary(self, run_id):
        return self.load_active(run_id).summary()

    def read_configuration(self) -> tuple[Connection | None, str | None]:
        """Read settings and their conflict token from the same atomic file version."""
        try:
            raw = self.record_path.read_bytes()
        except FileNotFoundError:
            return None, None
        try:
            record = Connection.model_validate(json.loads(raw))
        except (ValueError, OSError) as exc:
            raise ValueError(
                "Missing or invalid saved connection; configure or restore it explicitly"
            ) from exc
        if record.workspace != self.workspace:
            raise ValueError("Saved connection belongs to a different workspace")
        return record, hashlib.sha256(raw).hexdigest()

    def save_active(self, run_id: str, record: Connection) -> None:
        write_json(
            self.active_path, {"run_id": run_id, "connection": record.model_dump()}
        )

    def load_active(self, run_id: str) -> Connection:
        try:
            data = json.loads(self.active_path.read_text(encoding="utf-8"))
            if not run_id or data["run_id"] != run_id:
                raise ValueError("Active configuration identity mismatch")
            record = Connection.model_validate(data["connection"])
            if record.workspace != self.workspace:
                raise ValueError("Active configuration identity mismatch")
            return record
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Active configuration identity unavailable; stop and start this workspace once"
            ) from exc

    def configure(self, *, persist: bool = True, **options) -> Connection:
        """Low-level record construction; lifecycle callers use SetupSession."""
        record, _ = self.read_configuration()
        candidate = self.build(record, **options)
        if persist:
            write_json(self.record_path, candidate.model_dump())
        return candidate

    def build(
        self,
        base: Connection | None,
        *,
        public_origin=None,
        tunnel=None,
        port=None,
        ngrok_bin=None,
        ngrok_config=None,
        tools_config=None,
        import_connection: Path | None = None,
        clear_tools_config: bool = False,
        clear_ngrok_config: bool = False,
    ) -> Connection:
        """Construct a candidate without writing or re-reading a newer configuration."""
        if base is not None:
            if import_connection is not None:
                raise ValueError("Cannot import over an existing connection")
            data = base.model_dump()
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
                        "First setup requires --origin and a stable HTTPS entry"
                    )
                data = {
                    "workspace": self.workspace,
                    "public_origin": https_origin(public_origin),
                    "secret": secrets.token_urlsafe(32),
                }
        updates = {
            "public_origin": (
                https_origin(public_origin) if public_origin is not None else None
            ),
            "tunnel": tunnel,
            "port": port,
            "ngrok_bin": ngrok_bin,
            "ngrok_config": str(Path(ngrok_config).resolve()) if ngrok_config else None,
            "tools_config": str(Path(tools_config).resolve()) if tools_config else None,
        }
        data.update({key: value for key, value in updates.items() if value is not None})
        if clear_tools_config:
            if tools_config is not None:
                raise ValueError("Cannot set and clear the tool configuration together")
            data["tools_config"] = None
        if clear_ngrok_config:
            if ngrok_config is not None:
                raise ValueError(
                    "Cannot set and clear the ngrok configuration together"
                )
            data["ngrok_config"] = None
        if data.get("tunnel", "ngrok") == "external":
            if ngrok_bin is not None or ngrok_config is not None or clear_ngrok_config:
                raise ValueError("ngrok settings require ngrok mode")
            data.update(ngrok_bin="ngrok", ngrok_config=None)
        elif ngrok_bin and ("/" in ngrok_bin or "\\" in ngrok_bin):
            data["ngrok_bin"] = str(Path(ngrok_bin).expanduser().resolve())
        try:
            record = Connection.model_validate(data)
        except ValidationError as exc:
            raise ValueError("Invalid MCP connection settings") from exc
        if record.tools_config:
            config = load_config(Path(record.tools_config))
            if workspace_identity(config.workspace) != self.workspace:
                raise ValueError("Tool configuration belongs to a different workspace")
        return record

    def runtime(self) -> dict:
        try:
            data = json.loads(self.runtime_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}
