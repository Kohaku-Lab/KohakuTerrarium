"""Pure presentation helpers for the rich-CLI Drive surfaces.

Shared by the Drive record overlay and the Drives settings tab. Every state
carries a text/icon label plus a colour so status is never signalled by colour
alone (design §12.1). All functions here are pure — no service, no I/O — so the
renderers and their unit tests stay deterministic.
"""

from datetime import datetime, timezone
from typing import Any

# status value -> (icon, short label, rich style). Icons are ASCII/emoji-free
# glyphs that also read on a monochrome terminal; the label is the real signal.
_STATUS_META: dict[str, tuple[str, str, str]] = {
    "draft": ("○", "draft", "bright_black"),
    "active": ("●", "active", "green"),
    "waiting": ("◔", "waiting", "cyan"),
    "blocked": ("▲", "blocked", "red"),
    "paused": ("‖", "paused", "yellow"),
    "completed": ("✓", "completed", "bright_green"),
    "failed": ("✗", "failed", "bright_red"),
    "cancelled": ("⊘", "cancelled", "bright_black"),
    "retired": ("⊟", "retired", "bright_black"),
}

_AVAILABILITY_LABEL: dict[str, str] = {
    "registration_disabled": "registration disabled",
    "registration_unavailable": "registration unavailable",
    "registration_incompatible": "registration incompatible",
}

# Terminal statuses — used to decide default selection + terminal styling.
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "retired"})

_NON_TERMINAL = frozenset({"draft", "active", "waiting", "blocked", "paused"})

# UI actions shared by the rich-CLI overlay and the TUI panel. ``gate`` is the
# server ``allowed_actions`` string that must be present; ``statuses`` narrows
# the offer to states where the operation is meaningful; ``target`` is the
# transition target status value (``None`` for wake / progress which have their
# own service methods). Both surfaces gate on these so capability filtering
# stays in one place — the server re-authorizes regardless.
ACTIONS: list[dict[str, Any]] = [
    {
        "id": "pause",
        "key": "p",
        "label": "pause",
        "gate": "transition",
        "statuses": frozenset({"active", "waiting"}),
        "target": "paused",
    },
    {
        "id": "resume",
        "key": "r",
        "label": "resume",
        "gate": "transition",
        "statuses": frozenset({"paused", "blocked"}),
        "target": "active",
    },
    {
        "id": "wake",
        "key": "w",
        "label": "wake",
        "gate": "transition",
        "statuses": frozenset({"waiting"}),
        "target": None,
    },
    {
        "id": "cancel",
        "key": "c",
        "label": "cancel",
        "gate": "transition",
        "statuses": _NON_TERMINAL,
        "target": "cancelled",
    },
    {
        "id": "progress",
        "key": "g",
        "label": "log progress",
        "gate": "report_progress",
        "statuses": _NON_TERMINAL,
        "target": None,
    },
]


def action_enabled(row: dict[str, Any], action: dict[str, Any]) -> bool:
    """Whether ``action`` is offered for the Drive ``row`` (capability + state)."""
    if action["gate"] not in row.get("allowed_actions", ()):
        return False
    return row.get("status") in action["statuses"]


def enabled_actions(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Capability-filtered UI actions for a Drive row."""
    return [a for a in ACTIONS if action_enabled(row, a)]


def project_view(view: Any) -> dict[str, Any]:
    """Flatten a :class:`DriveView` into a plain display dict.

    Pulls the record fields the UI shows (including ``not_before``, which the
    wire ``to_dict`` omits) plus the folded assignment / availability / actions.
    Decouples the renderers + their tests from the dataclass shape.
    """
    record = view.record
    not_before = getattr(record, "not_before", None)
    return {
        "drive_id": record.drive_id,
        "kind": record.kind,
        "revision": record.revision,
        "title": record.title,
        "status": record.status.value,
        "status_reason": record.status_reason,
        "priority": record.priority,
        "scope_type": record.scope_type,
        "owner": record.owner.format(),
        "assignee_creature_id": view.assignee_creature_id,
        "assignment_state": view.assignment_state,
        "availability": view.availability,
        "durability": view.durability,
        "allowed_actions": tuple(view.allowed_actions),
        "not_before": not_before.isoformat() if not_before else None,
    }


def status_meta(status: str) -> tuple[str, str, str]:
    """(icon, label, style) for a status value; unknown -> neutral."""
    return _STATUS_META.get(status, ("•", status or "?", "white"))


def status_label(status: str) -> str:
    """``icon label`` for one status (colour applied by the caller)."""
    icon, label, _ = status_meta(status)
    return f"{icon} {label}"


def warning_badges(view: dict[str, Any]) -> list[tuple[str, str]]:
    """(label, style) badges for the non-nominal conditions of a Drive view.

    Covers derived-availability, orphaned assignment, and blocked/recovery so
    the list row can flag records that need attention without opening detail.
    """
    badges: list[tuple[str, str]] = []
    availability = view.get("availability", "available")
    if availability and availability != "available":
        label = _AVAILABILITY_LABEL.get(availability, availability)
        badges.append((label, "yellow"))
    if view.get("assignment_state") == "orphaned":
        badges.append(("orphaned", "red"))
    if view.get("status") == "blocked":
        badges.append(("needs attention", "red"))
    return badges


def owner_assignee(view: dict[str, Any]) -> str:
    """``owner -> assignee`` line for a Drive view."""
    owner = view.get("owner", "?")
    assignee = view.get("assignee_creature_id") or "(unassigned)"
    return f"{owner} -> {assignee}"


def next_wake(view: dict[str, Any]) -> str:
    """Human ``not_before`` summary for a waiting Drive, or ''."""
    raw = view.get("not_before")
    if not raw:
        return ""
    dt = _parse_dt(raw)
    if dt is None:
        return str(raw)
    now = datetime.now(timezone.utc)
    delta = (dt - now).total_seconds()
    if delta <= 0:
        return "ready now"
    if delta < 60:
        return f"in {int(delta)}s"
    if delta < 3600:
        return f"in {int(delta // 60)}m"
    if delta < 86400:
        return f"in {int(delta // 3600)}h"
    return f"in {int(delta // 86400)}d"


def durability_label(view: dict[str, Any]) -> str:
    """``persistent`` / ``ephemeral`` durability text for a Drive view."""
    return str(view.get("durability") or "unknown")


def _parse_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


__all__ = [
    "ACTIONS",
    "TERMINAL_STATUSES",
    "action_enabled",
    "durability_label",
    "enabled_actions",
    "next_wake",
    "owner_assignee",
    "project_view",
    "status_label",
    "status_meta",
    "warning_badges",
]
