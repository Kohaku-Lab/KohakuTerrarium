"""Optimistic configuration sessions: prepare, review, then atomically commit."""

from dataclasses import dataclass

from kohakuterrarium.mcp_server.connection import (
    Connection,
    ConnectionStore,
    validate_dependencies,
    write_json,
)
from kohakuterrarium.mcp_server.service import is_running, lifecycle_command, status


@dataclass
class SetupSession:
    store: ConnectionStore
    original: Connection | None
    revision: str | None

    @classmethod
    def open(cls, store: ConnectionStore) -> "SetupSession":
        original, revision = store.read_configuration()
        return cls(store, original, revision)

    def prepare(self, **options) -> Connection:
        candidate = self.store.build(self.original, **options)
        validate_dependencies(candidate)
        return candidate

    def save(self, candidate: Connection) -> dict:
        with lifecycle_command(self.store):
            _, revision = self.store.read_configuration()
            if revision != self.revision:
                raise ValueError(
                    "Configuration changed in another command; run setup again"
                )
            if candidate.workspace != self.store.workspace or (
                self.original and candidate.secret != self.original.secret
            ):
                raise ValueError(
                    "Setup cannot change workspace identity or rotate its secret"
                )
            # Older live processes reread saved settings when reconnecting.
            # Never let a new CLI silently change their running ingress.
            if is_running(self.store):
                self.store.load_active(self.store.runtime().get("run_id", ""))
            validate_dependencies(candidate)
            write_json(self.store.record_path, candidate.model_dump())
            current = status(self.store)
            return {
                "state": "configured",
                "running": current["running"],
                "configuration": candidate.summary(),
                "restart_required": current["restart_required"],
                "pending_changes": current["pending_changes"],
                "origin_changed": bool(
                    self.original
                    and self.original.public_origin != candidate.public_origin
                ),
            }
