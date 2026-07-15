"""Contribute bounded Drive guidance to creature system prompts.

The plugin combines a generic Drive contract with normalized prose from enabled
registrations in stable name order. Current Drive records stay out of the system
prompt and arrive through event projections or status tools instead.
"""

from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot

# This contract stays kind-agnostic and bounded because registration-specific
# guidance is appended separately.
_GENERIC_CONTRACT = (
    "## Drive runtime\n"
    "A drive is a durable, assignable commitment the runtime delivers to you as "
    "an ordinary event. You interpret it, act with your normal tools, and report "
    "outcomes — the runtime owns identity, delivery, and recovery.\n"
    "- Ownership: you may create and manage your own drives; a drive owned by "
    "someone else that is merely assigned to you is report/propose-only — do not "
    "cancel, reassign, or retire it.\n"
    "- Recovery: a redelivered drive may follow an attempt that already ran side "
    "effects. Inspect current state and reconcile before repeating actions.\n"
    "- Delivery: the active Drive's projected objective and identity arrive in "
    "the event that starts the turn. Act on that projection directly; do not begin "
    "by listing or inspecting Drives unless required information is actually missing.\n"
    "- Progress: report material progress with evidence rather than narrating.\n"
    "- Completion: propose completion or failure with evidence for verification; "
    "do not assert a terminal outcome yourself.\n"
    "- Budgets: obey wait/pause policy and any declared budgets; exhausting a "
    "budget pauses or blocks a drive, it never means success."
)


class DriveRuntimePromptPlugin(BasePlugin):
    """Add generic and registration-specific Drive guidance to the prompt."""

    name = "drive_runtime"
    description = "Bounded Drive runtime guidance from enabled registrations."
    # Runtime guidance belongs after tool instructions and before framework hints.
    priority = 55

    def __init__(self, snapshot: EnabledRegistrySnapshot | None = None) -> None:
        super().__init__()
        self._snapshot = snapshot

    def set_snapshot(self, snapshot: EnabledRegistrySnapshot | None) -> None:
        """Replace the enabled-registration snapshot used for prompt content."""
        self._snapshot = snapshot

    def get_prompt_content(self, context: PluginContext) -> str | None:
        parts: list[str] = [_GENERIC_CONTRACT]
        snapshot = self._snapshot
        if snapshot is not None:
            for name, text in snapshot.prompt_contributions():
                parts.append(f"### Drive kind: {name}\n{text}")
        return "\n\n".join(parts)

    def runtime_services(self, context: Any) -> dict[str, Any]:
        # The graph environment owns the Drive service handle, so this plugin
        # exposes no per-call runtime services.
        return {}


__all__ = ["DriveRuntimePromptPlugin"]
