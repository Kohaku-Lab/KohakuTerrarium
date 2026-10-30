"""Session metadata: identity, status and conversation-open marker."""

import platform
from datetime import datetime, timezone
from typing import Any

from kohakuterrarium.session.store.identity import (
    legacy_conversation_id,
    new_conversation_id,
)
from kohakuterrarium.session.vault.io import key_text, read_prefix
from kohakuterrarium.session.store.lifecycle.version import FORMAT_VERSION


class StoreMetaMixin:
    """Read and write the ``meta`` table."""

    # Owned by the handle part.
    _path: str
    meta: Any

    def init_meta(
        self,
        session_id: str,
        config_type: str,
        config_path: str,
        pwd: str,
        agents: list[str],
        config_snapshot: dict | None = None,
        terrarium_name: str | None = None,
        terrarium_channels: list[dict] | None = None,
        terrarium_creatures: list[dict] | None = None,
    ) -> None:
        """Initialize session metadata. Called once when session is created.

        ``config_type`` must be a supported resume discriminator.
        """
        if config_type not in ("agent", "terrarium"):
            raise ValueError(
                f"config_type must be 'agent' or 'terrarium', got "
                f"{config_type!r} — resume dispatches on this value"
            )

        now = datetime.now(timezone.utc).isoformat()

        self.meta["session_id"] = session_id
        self.meta["format_version"] = FORMAT_VERSION
        self.meta["config_type"] = config_type
        self.meta["config_path"] = config_path
        self.meta["config_snapshot"] = config_snapshot or {}
        self.meta["pwd"] = pwd
        self.meta["created_at"] = now
        self.meta["last_active"] = now
        self.meta["status"] = "running"
        self.meta["conversation_open"] = True
        if not self.meta.get("conversation_id"):
            self.meta["conversation_id"] = new_conversation_id()
        self.meta["agents"] = agents
        self.meta["hostname"] = platform.node()
        self.meta["python_version"] = platform.python_version()

        if terrarium_name:
            self.meta["terrarium_name"] = terrarium_name
        if terrarium_channels:
            self.meta["terrarium_channels"] = terrarium_channels
        if terrarium_creatures:
            self.meta["terrarium_creatures"] = terrarium_creatures

    def update_status(self, status: str) -> None:
        """Update session status (running, paused, completed, crashed)."""

        self.meta["status"] = status
        self.meta["last_active"] = datetime.now(timezone.utc).isoformat()

    def set_conversation_open(self, is_open: bool) -> None:
        """Persist whether the user still considers this conversation open."""

        self.meta["conversation_open"] = bool(is_open)
        if is_open:
            self.ensure_conversation_id()

    def ensure_conversation_id(self) -> str | None:
        """Return the immutable persisted identity for a marked conversation.

        Legacy files are backfilled only when they carry the explicit open
        marker. Their path-derived UUID is deterministic across retries.
        """
        existing = self.meta.get("conversation_id")
        if isinstance(existing, str) and existing:
            return existing
        if not self.meta.get("conversation_open"):
            return None
        conversation_id = legacy_conversation_id(self._path)
        self.meta["conversation_id"] = conversation_id
        return conversation_id

    def set_viewer_default_agent(self, namespace: str) -> None:
        """Record ``namespace`` as the session viewer's default agent.

        The most recent attachment wins. The value remains separate from
        ``meta["agents"]`` so main-creature discovery and resume are unaffected.
        """

        if not isinstance(namespace, str) or not namespace:
            return
        self.meta["viewer_default_agent"] = namespace

    def touch(self) -> None:
        """Update last_active timestamp."""

        self.meta["last_active"] = datetime.now(timezone.utc).isoformat()

    def load_meta(self) -> dict[str, Any]:
        """Load all metadata as a dict.

        Event namespaces augment ``meta["agents"]`` so hot-plugged creatures are
        visible to resume even when metadata was not updated explicitly.
        """
        result = {
            key_text(key): value
            for key, value in read_prefix(self.meta, what="meta key")
        }
        known = list(result.get("agents") or [])
        discovered = self.discover_agents_from_events()
        for name in discovered:
            if name not in known:
                known.append(name)
        result["agents"] = known
        return result
