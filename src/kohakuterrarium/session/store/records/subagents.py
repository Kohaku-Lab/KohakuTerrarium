"""Sub-agent run metadata and conversations."""

import time
from typing import Any

from kohakuterrarium.session.store.events.counters import restore_subagent_counters
from kohakuterrarium.session.vault.io import key_text, read_values, scan_keys


class SubagentRecordsMixin:
    """Persist sub-agent runs keyed ``<parent>:<name>:<run>``."""

    # Table opened by the handle part.
    subagents: Any

    def _init_subagents(self) -> None:
        self._subagent_runs: dict[str, int] = {}

    def _restore_subagent_counters(self) -> None:
        restore_subagent_counters(self.subagents, self._subagent_runs)

    def next_subagent_run(self, parent: str, name: str) -> int:
        """Get the next run index for a sub-agent."""
        sa_key = f"{parent}:{name}"
        run = self._subagent_runs.get(sa_key, 0)
        self._subagent_runs[sa_key] = run + 1
        return run

    def save_subagent(
        self,
        parent: str,
        name: str,
        run: int,
        meta: dict,
        conv_json: str | None = None,
    ) -> None:
        """Save sub-agent run metadata and optional conversation."""
        prefix = f"{parent}:{name}:{run}"
        if "ts" not in meta:
            meta["ts"] = time.time()
        self.subagents[f"{prefix}:meta"] = meta
        if conv_json is not None:
            self.subagents[f"{prefix}:conversation"] = conv_json

    def load_subagent_meta(self, parent: str, name: str, run: int) -> dict | None:
        """Load sub-agent run metadata."""
        try:
            return self.subagents[f"{parent}:{name}:{run}:meta"]
        except KeyError:
            return None

    def load_subagent_conversation(
        self, parent: str, name: str, run: int
    ) -> str | None:
        """Load sub-agent conversation JSON."""
        try:
            val = self.subagents[f"{parent}:{name}:{run}:conversation"]
            return val.decode() if isinstance(val, bytes) else val
        except KeyError:
            return None

    def list_subagent_runs(
        self, *, parent: str | None = None, name: str | None = None
    ) -> list[dict[str, Any]]:
        """Return persisted sub-agent run metadata in key order."""
        wanted: list[str] = []
        identities: dict[str, tuple[str, str, int]] = {}
        for key in map(key_text, scan_keys(self.subagents)):
            if not key.endswith(":meta"):
                continue
            identity = key[: -len(":meta")].rsplit(":", 2)
            if len(identity) != 3:
                continue
            entry_parent, entry_name, run_text = identity
            try:
                run = int(run_text)
            except ValueError:
                continue
            if parent is not None and entry_parent != parent:
                continue
            if name is not None and entry_name != name:
                continue
            wanted.append(key)
            identities[key] = (entry_parent, entry_name, run)
        rows: list[dict[str, Any]] = []
        for key, meta in read_values(self.subagents, wanted, what="sub-agent run"):
            entry_parent, entry_name, run = identities[key]
            rows.append(
                {
                    "parent": entry_parent,
                    "name": entry_name,
                    "run": run,
                    "job_id": meta.get("job_id") if isinstance(meta, dict) else None,
                    "meta": meta,
                }
            )
        return rows

    def find_subagent_run(
        self, job_id: str, *, parent: str | None = None
    ) -> dict[str, Any] | None:
        """Return the persisted run carrying an exact job identifier."""
        if not job_id:
            return None
        for row in self.list_subagent_runs(parent=parent):
            if row.get("job_id") == job_id:
                return row
        return None
