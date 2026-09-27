"""Optimistic configuration sessions: prepare, review, then atomically commit."""

from dataclasses import dataclass

from kohakuterrarium.mcp_server.endpoint import Endpoint, EndpointStore
from kohakuterrarium.mcp_server.records import lifecycle_command, write_json
from kohakuterrarium.mcp_server.service import is_running, status


@dataclass
class SetupSession:
    store: EndpointStore
    original: Endpoint | None
    revision: str | None

    @classmethod
    def open(cls, store: EndpointStore) -> "SetupSession":
        original, revision = store.read_configuration()
        return cls(store, original, revision)

    def prepare(self, **options) -> Endpoint:
        candidate = self.store.build(self.original, **options)
        self.store.validate(candidate)
        return candidate

    def save(self, candidate: Endpoint) -> dict:
        with lifecycle_command(self.store.command_lock):
            _, revision = self.store.read_configuration()
            if revision != self.revision:
                raise ValueError(
                    "Configuration changed in another command; run setup again"
                )
            if not self.store.owns(candidate) or (
                self.original and candidate.secret != self.original.secret
            ):
                raise ValueError(
                    "Setup cannot change endpoint identity or rotate its secret"
                )
            # Older live processes reread saved settings when reconnecting.
            # Never let a new CLI silently change their running ingress.
            if is_running(self.store):
                self.store.load_active(self.store.runtime().get("run_id", ""))
            self.store.validate(candidate)
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
