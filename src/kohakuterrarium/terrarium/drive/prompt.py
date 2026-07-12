"""The Drive runtime prompt contributor (design §8.7, §9.3).

:class:`DriveRuntimePromptPlugin` is an ordinary :class:`BasePlugin` — not a
lifecycle authority — that a Drive-enabled Terrarium injects into every creature.
It contributes one bounded generic Drive contract plus the pre-normalized prose
from the enabled registrations, in stable registration-name order, reading an
immutable :class:`EnabledRegistrySnapshot` handle supplied by the Drive runtime.

It never dumps current Drive records: those arrive through bounded ``drive_ready``
event projections and the ``drive_status`` tool. A registry swap (reconfigure)
replaces the handle via :meth:`set_snapshot` and refreshes live agents.
"""

from typing import Any

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot

# Bounded, kind-agnostic Drive contract. Guidance the model needs whenever the
# generic self-service Drive tools are injected — ownership, recovery, and the
# propose-don't-assert / obey-budgets discipline (design §9.3). Kept short.
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
    "- Progress: report material progress with evidence rather than narrating.\n"
    "- Completion: propose completion or failure with evidence for verification; "
    "do not assert a terminal outcome yourself.\n"
    "- Budgets: obey wait/pause policy and any declared budgets; exhausting a "
    "budget pauses or blocks a drive, it never means success."
)


class DriveRuntimePromptPlugin(BasePlugin):
    """Contributes the generic Drive contract + enabled-registration prose."""

    name = "drive_runtime"
    description = "Bounded Drive runtime guidance from enabled registrations."
    # After tool guidance, before framework hints — same slot as other runtime
    # prompt contributors.
    priority = 55

    def __init__(self, snapshot: EnabledRegistrySnapshot | None = None) -> None:
        super().__init__()
        self._snapshot = snapshot

    def set_snapshot(self, snapshot: EnabledRegistrySnapshot | None) -> None:
        """Swap the enabled-registry snapshot after an applied reconfigure.

        The caller refreshes live agents' system prompts afterwards; the new
        prose is produced on the next :meth:`get_prompt_content`."""
        self._snapshot = snapshot

    def get_prompt_content(self, context: PluginContext) -> str | None:
        parts: list[str] = [_GENERIC_CONTRACT]
        snapshot = self._snapshot
        if snapshot is not None:
            for name, text in snapshot.prompt_contributions():
                parts.append(f"### Drive kind: {name}\n{text}")
        return "\n\n".join(parts)

    def runtime_services(self, context: Any) -> dict[str, Any]:
        # The Drive service handle is exposed through the graph environment,
        # not per-call plugin services; this stays empty by design.
        return {}


__all__ = ["DriveRuntimePromptPlugin"]
