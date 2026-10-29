"""Configuration-root scoped ingress and immutable startup tool snapshots."""

import base64
import hashlib
import json
import secrets
import shutil
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kohakuterrarium.mcp_server.config import GlobalToolsConfig, load_global_config
from kohakuterrarium.mcp_server.records import (
    https_origin,
    read_json,
    workspace_identity,
    write_json,
)
from kohakuterrarium.mcp_server.workspaces import WorkspaceRegistry
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.file_lock import FileLock


class Endpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[2] = 2
    home_dir: str
    public_origin: str
    secret: str = Field(repr=False, pattern=r"^[A-Za-z0-9_-]{43,128}$")
    port: int = Field(default=8765, ge=1, le=65535)
    tunnel: Literal["ngrok", "cloudflare", "external"] = "ngrok"
    ngrok_bin: str = "ngrok"
    ngrok_config: str | None = None
    cloudflared_bin: str = "cloudflared"
    cloudflare_tunnel_id: str | None = None
    cloudflare_credentials: str | None = None
    tools_config: str | None = None
    probe_proxy: str | None = None

    @field_validator("public_origin")
    @classmethod
    def validate_origin(cls, value):
        return https_origin(value)

    @field_validator("probe_proxy")
    @classmethod
    def validate_probe_proxy(cls, value):
        if value is None:
            return None
        try:
            parsed = urlsplit(value)
            if (
                any(ord(char) <= 32 or ord(char) == 127 for char in value)
                or parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "::1"}
                or not parsed.port
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("invalid proxy")
        except ValueError:
            raise ValueError(
                "Probe proxy must be a local HTTP proxy URL without credentials"
            ) from None
        return value.rstrip("/")

    @property
    def url(self):
        return self.public_origin + "/mcp/" + self.secret

    def serialized(self):
        excluded = set()
        if self.probe_proxy is None:
            excluded.add("probe_proxy")
        if self.tunnel != "cloudflare":
            excluded.update(
                {
                    "cloudflared_bin",
                    "cloudflare_tunnel_id",
                    "cloudflare_credentials",
                }
            )
        return self.model_dump(exclude=excluded)

    def summary(self):
        return self.model_dump(exclude={"secret", "version"})


class EndpointStore:
    """One unified endpoint and workspace registry per KT configuration root."""

    def __init__(self, home_dir: Path | None = None):
        self.home_dir = (home_dir or config_dir()).expanduser().resolve()
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.directory = self.home_dir / "mcp-serve" / "endpoint"
        self.record_path = self.directory / "connection.json"
        self.runtime_path = self.directory / "runtime.json"
        self.active_path = self.directory / "active.json"
        self.stop_path = self.directory / "stop.json"
        self.control_path = self.directory / "control.json"
        self.response_path = self.directory / "response.json"
        self.command_lock = FileLock(self.directory / "command.lock")
        self.instance_lock = FileLock(self.directory / "instance.lock")
        self.registry = WorkspaceRegistry(self.directory / "workspaces.json")

    @property
    def server_arguments(self):
        return ["--home-dir", str(self.home_dir)]

    @property
    def process_directory(self):
        return str(self.home_dir)

    def owns(self, record):
        return record.home_dir == workspace_identity(self.home_dir)

    def read_configuration(self):
        try:
            raw = self.record_path.read_bytes()
        except FileNotFoundError:
            return None, None
        try:
            record = Endpoint.model_validate(json.loads(raw))
            if not self.owns(record):
                raise ValueError("different environment")
        except ValueError:
            raise ValueError("Invalid saved endpoint; restore it explicitly") from None
        return record, hashlib.sha256(raw).hexdigest()

    def load(self):
        record, _ = self.read_configuration()
        if record is None:
            raise ValueError("No saved endpoint; run kt mcp-serve setup first")
        return record

    def configure(self, **options):
        record, _ = self.read_configuration()
        candidate = self.build(record, **options)
        write_json(self.record_path, candidate.serialized())
        return candidate

    def build(self, base, **options):
        if options.get("import_connection"):
            raise ValueError("Use kt mcp-serve migrate for legacy connections")
        data = (
            base.model_dump()
            if base
            else {
                "home_dir": workspace_identity(self.home_dir),
                "secret": secrets.token_urlsafe(32),
            }
        )
        for name in (
            "public_origin",
            "tunnel",
            "port",
            "ngrok_bin",
            "ngrok_config",
            "cloudflared_bin",
            "cloudflare_tunnel_id",
            "cloudflare_credentials",
            "tools_config",
            "probe_proxy",
        ):
            value = options.get(name)
            if value is not None:
                if name in {"tools_config", "ngrok_config", "cloudflare_credentials"}:
                    value = str(Path(value).expanduser().resolve())
                if name in {"ngrok_bin", "cloudflared_bin"} and (
                    "/" in value or "\\" in value
                ):
                    value = str(Path(value).expanduser().resolve())
                data[name] = value
        if "public_origin" not in data:
            raise ValueError("First setup requires --origin")
        for flag, name in (
            ("clear_tools_config", "tools_config"),
            ("clear_ngrok_config", "ngrok_config"),
            ("clear_probe_proxy", "probe_proxy"),
        ):
            if options.get(flag):
                if options.get(name) is not None:
                    raise ValueError("Cannot set and clear a configuration together")
                data[name] = None
        mode = data.get("tunnel", "ngrok")
        if mode != "ngrok":
            if any(
                options.get(key)
                for key in ("ngrok_bin", "ngrok_config", "clear_ngrok_config")
            ):
                raise ValueError("ngrok settings require ngrok mode")
            data.update(ngrok_bin="ngrok", ngrok_config=None)
        if mode != "cloudflare":
            if any(
                options.get(key)
                for key in (
                    "cloudflared_bin",
                    "cloudflare_tunnel_id",
                    "cloudflare_credentials",
                )
            ):
                raise ValueError("Cloudflare settings require cloudflare mode")
            data.update(
                cloudflared_bin="cloudflared",
                cloudflare_tunnel_id=None,
                cloudflare_credentials=None,
            )
        try:
            return Endpoint.model_validate(data)
        except ValueError:
            raise ValueError("Invalid MCP endpoint settings") from None

    def validate(self, record):
        self.tools(record)
        if record.tunnel == "ngrok":
            if not shutil.which(record.ngrok_bin):
                raise ValueError("ngrok executable not found")
            if record.ngrok_config:
                with Path(record.ngrok_config).open("rb") as stream:
                    stream.read(1)
        if record.tunnel == "cloudflare":
            if not shutil.which(record.cloudflared_bin):
                raise ValueError("cloudflared executable not found")
            if not record.cloudflare_tunnel_id or not record.cloudflare_credentials:
                raise ValueError(
                    "Cloudflare tunnel ID and credentials file are required"
                )
            try:
                tunnel_id = UUID(record.cloudflare_tunnel_id)
                with Path(record.cloudflare_credentials).open(
                    "r", encoding="utf-8"
                ) as stream:
                    credentials = json.load(stream)
                if (
                    not isinstance(credentials, dict)
                    or UUID(credentials["TunnelID"]) != tunnel_id
                    or not isinstance(credentials.get("AccountTag"), str)
                    or not credentials["AccountTag"]
                    or not isinstance(credentials.get("TunnelSecret"), str)
                    or len(base64.b64decode(credentials["TunnelSecret"], validate=True))
                    < 32
                ):
                    raise ValueError("credentials mismatch")
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ValueError(
                    "Invalid Cloudflare tunnel ID or credentials file"
                ) from None

    def tools(self, record):
        return (
            load_global_config(Path(record.tools_config))
            if record.tools_config
            else GlobalToolsConfig()
        )

    @staticmethod
    def _tools_revision(config):
        return hashlib.sha256(
            json.dumps(
                config.model_dump(mode="json", by_alias=True), sort_keys=True
            ).encode()
        ).hexdigest()

    def configuration_summary(self, record):
        try:
            revision = self._tools_revision(self.tools(record))
        except (OSError, ValueError):
            revision = "invalid"
        return {**record.summary(), "tools_revision": revision}

    def active_summary(self, run_id):
        return {
            **self.load_active(run_id).summary(),
            "tools_revision": self._tools_revision(self.active_tools(run_id)),
        }

    def save_active(self, run_id, record):
        write_json(
            self.active_path,
            {
                "run_id": run_id,
                "connection": record.serialized(),
                "tools": self.tools(record).model_dump(by_alias=True),
            },
        )

    def _active(self, run_id):
        try:
            data = read_json(self.active_path)
            record = Endpoint.model_validate(data["connection"])
            if not run_id or data["run_id"] != run_id or not self.owns(record):
                raise ValueError("identity")
            return data, record
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError(
                "Active configuration unavailable; stop and start the endpoint"
            ) from None

    def load_active(self, run_id):
        return self._active(run_id)[1]

    def active_tools(self, run_id):
        return GlobalToolsConfig.model_validate(self._active(run_id)[0]["tools"])

    def runtime(self):
        try:
            data = read_json(self.runtime_path)
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}
