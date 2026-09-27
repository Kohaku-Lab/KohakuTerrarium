"""Explicit stopped-source migration to a new unified endpoint identity."""

from pathlib import Path

from kohakuterrarium.mcp_server.config import load_config
from kohakuterrarium.mcp_server.connection import (
    ConnectionStore,
    workspace_identity,
    write_json,
)
from kohakuterrarium.mcp_server.service import lifecycle_command
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


def migrate(store, workspace: Path, name: str, legacy_state_dir: Path | None = None):
    source = ConnectionStore(
        workspace, legacy_state_dir or store.home_dir / "mcp-serve"
    )
    with lifecycle_command(store), lifecycle_command(source):
        if store.record_path.exists() or store.registry.read():
            raise ValueError("Migration requires an unconfigured, empty endpoint")
        try:
            with (
                FileLock(source.instance_lock.path),
                FileLock(store.instance_lock.path),
            ):
                old = source.load()
                options = old.model_dump(exclude={"workspace", "version", "secret"})
                if old.tunnel == "external":
                    options.pop("ngrok_bin")
                    options.pop("ngrok_config")
                if old.tools_config:
                    config = load_config(Path(old.tools_config))
                    if workspace_identity(config.workspace) != workspace_identity(
                        workspace
                    ):
                        raise ValueError(
                            "Legacy tool configuration belongs to another workspace"
                        )
                    for plugin in config.plugins:
                        if plugin.module and not plugin.module.startswith("@"):
                            plugin.module = str(
                                (config.workspace / plugin.module).resolve()
                            )
                    converted = store.directory / "migrated-tools.json"
                    write_json(
                        converted,
                        config.model_dump(
                            mode="json", by_alias=True, exclude={"workspace"}
                        ),
                    )
                    options["tools_config"] = converted
                candidate = store.build(None, **options)
                store.validate(candidate)
                entry = store.registry.add(name, workspace)
                try:
                    write_json(store.record_path, candidate.model_dump())
                except BaseException:
                    store.registry.remove(name, entry.registration_id)
                    raise
                return {
                    "state": "stopped",
                    "running": False,
                    "migrated": True,
                    "workspace_id": name,
                    "home_dir": str(store.home_dir),
                    "new_credentials": True,
                }
        except FileLockBusy:
            raise RuntimeError(
                "Stop the legacy source and target endpoint before migrating; no process was stopped"
            ) from None
