"""Read-only version-one connection records for explicit stopped-source migration."""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kohakuterrarium.mcp_server.records import https_origin, workspace_identity
from kohakuterrarium.utils.file_lock import FileLock


class LegacyConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
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


class LegacyConnectionSource:
    """Locate and validate a legacy record while exposing its migration locks."""

    def __init__(self, workspace: Path, state_dir: Path):
        workspace = workspace.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.workspace = workspace_identity(workspace)
        key = hashlib.sha256(self.workspace.encode()).hexdigest()[:32]
        directory = state_dir.expanduser().resolve() / key
        self.record_path = directory / "connection.json"
        self.command_lock = FileLock(directory / "command.lock")
        self.instance_lock = FileLock(directory / "instance.lock")

    def load(self) -> LegacyConnection:
        try:
            raw = self.record_path.read_bytes()
        except FileNotFoundError:
            raise ValueError("No saved legacy connection to migrate") from None
        try:
            record = LegacyConnection.model_validate(json.loads(raw))
        except ValueError:
            raise ValueError(
                "Invalid legacy connection; restore it before migrating"
            ) from None
        if record.workspace != self.workspace:
            raise ValueError("Legacy connection belongs to a different workspace")
        return record
