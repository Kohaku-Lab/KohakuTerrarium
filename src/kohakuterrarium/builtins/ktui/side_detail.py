"""Detail views in the Chat page's side panel, as the web's side views: a sub-agent's live
trace, a running job, a drive with its progress. Picking an entry of the panel opens its
detail in place of the lists; ``‹ back`` returns to them. The first list of a detail holds its
actions (stop, move to the background, pause / resume / wake / cancel a drive, log progress).
"""

import time
from typing import TYPE_CHECKING, Any

from kohakutui.ui import Picker, Prompt

from kohakuterrarium.builtins.cli_rich.dialogs.drive.format import (
    enabled_actions,
    next_wake,
    owner_assignee,
    project_view,
    status_label,
)
from kohakuterrarium.builtins.ktui.format import duration, one_line
from kohakuterrarium.builtins.ktui.tool_bodies import image_cell, media_cell
from kohakuterrarium.builtins.ktui.trace import trace_body
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.builtins.ktui.remote_drives import drive_service
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.builtins.ktui.shell import KtuiShell

logger = get_logger(__name__)

BACK = {"label": "‹ back", "value": "side:back", "tone": "info"}
PROGRESS_SHOWN = 12
# a picture drawn in the side panel
ARTIFACT_WIDTH = 30


class SideDetail:
    """One open detail: ``trace`` / ``job`` (creature id, job id) or ``drive`` (drive id)."""

    def __init__(self, shell: "KtuiShell", kind: str, *ids: str):
        self.shell = shell
        self.kind = kind
        self.ids = ids
        self.version = 0
        self._stop_listening: Any = None
        self._drive: dict | None = None
        self._view: Any = None
        self._progress: list[Any] = []
        self._deliveries: list[Any] = []
        self._error = ""
        if kind == "trace":
            entry = shell.entries.get(ids[0])
            if entry is not None:
                self._stop_listening = entry.view.traces.listen(self._changed)
        elif kind == "drive":
            shell.spawn(self.load_drive())

    def _changed(self, _trace: Any) -> None:
        self.version += 1
        self.shell.chrome.side.refresh()

    def close(self) -> None:
        if self._stop_listening is not None:
            self._stop_listening()
            self._stop_listening = None

    # -- what it shows --------------------------------------------------

    def sections(self) -> list[tuple[str, str | None, str, Any]]:
        match self.kind:
            case "trace":
                return self._trace()
            case "job":
                return self._job()
            case "drive":
                return self._drive_sections()
            case "art":
                return self._artifact()
        return [("actions", None, "tree", [BACK])]

    def _artifact(self) -> list:
        cid, index = self.ids
        entry = self.shell.entries.get(cid)
        art = entry.view.artifacts if entry else []
        if not index.isdigit() or int(index) >= len(art):
            return [("actions", None, "tree", [BACK]), ("gone", None, "text", "gone")]
        kind, url, name = art[int(index)]
        body = (
            image_cell(url, name, width=ARTIFACT_WIDTH, place=entry.view.media)
            if kind == "image"
            else media_cell(url, name, kind, entry.view.media)
        )
        return [
            ("actions", None, "tree", [BACK]),
            ("body", f"{kind} · {name or 'untitled'}", "spec", (f"art:{index}", body)),
        ]

    def _trace(self) -> list:
        cid, job_id = self.ids
        entry = self.shell.entries.get(cid)
        trace = entry.view.traces.get(job_id) if entry else None
        if trace is None:
            return [
                ("actions", None, "tree", [BACK]),
                ("gone", None, "text", "no longer listed"),
            ]
        actions = [BACK]
        if trace.running:
            actions.append({"label": "■ stop", "value": "side:stop", "tone": "warn"})
        title = f"◆ {trace.name} · {entry.view.name}"
        body = trace_body(trace, entry.view.batch_size)
        return [
            ("actions", None, "tree", actions),
            ("body", title, "spec", (f"trace:{job_id}:{self.version}", body)),
        ]

    def _job(self) -> list:
        cid, job_id = self.ids
        entry = self.shell.entries.get(cid)
        job = entry.view.jobs.get(job_id) if entry else None
        if job is None:
            return [
                ("actions", None, "tree", [BACK]),
                ("gone", None, "text", "finished"),
            ]
        actions = [BACK, {"label": "■ stop", "value": "side:stop", "tone": "warn"}]
        if not job.background:
            actions.append(
                {"label": "⏵ move to background", "value": "side:background"}
            )
        fields = [
            {"label": "agent", "value": entry.view.name},
            {"label": "running", "value": duration(time.monotonic() - job.started)},
            {
                "label": "where",
                "value": "background" if job.background else "foreground",
            },
        ]
        return [
            ("actions", None, "tree", actions),
            ("body", f"⚙ {job.name}", "kv", fields),
            ("detail", None, "text", job.detail or ""),
        ]

    def _drive_sections(self) -> list:
        actions = [BACK]
        row = self._drive
        if row is None:
            text = self._error or "loading…"
            return [("actions", None, "tree", actions), ("body", None, "text", text)]
        for action in enabled_actions(row):
            actions.append(
                {"label": action["label"], "value": f"side:drive:{action['id']}"}
            )
        if row.get("status") not in ("completed", "failed", "cancelled", "retired"):
            actions.append({"label": "assign to…", "value": "side:drive:assign"})
            if row.get("assignee_creature_id"):
                actions.append({"label": "unassign", "value": "side:drive:unassign"})
        view = self._view
        fields = [
            {"label": "status", "value": status_label(str(row["status"]))},
            {"label": "kind", "value": str(row["kind"])},
            {"label": "who", "value": owner_assignee(row)},
            {"label": "next wake", "value": next_wake(row) or "—"},
            {"label": "id", "value": str(row["drive_id"])},
        ]
        if row.get("status_reason"):
            fields.append(
                {"label": "reason", "value": one_line(row["status_reason"], 80)}
            )
        spec = getattr(getattr(view, "record", None), "spec", None) or {}
        objective = spec.get("objective") if isinstance(spec, dict) else None
        progress = [
            {
                "label": one_line(p.summary, 70),
                "detail": p.created_at.strftime("%m-%d %H:%M"),
                "value": "side:none",
            }
            for p in self._progress[-PROGRESS_SHOWN:]
        ]
        out = [
            ("actions", None, "tree", actions),
            ("body", one_line(str(row["title"]), 60), "kv", fields),
        ]
        if objective:
            out.append(("objective", "Objective", "text", str(objective)))
        if progress:
            out.append(
                ("progress", f"Progress · {len(self._progress)}", "tree", progress)
            )
        deliveries = [self._delivery(d) for d in self._deliveries[-PROGRESS_SHOWN:]]
        if deliveries:
            title = f"Deliveries · {len(self._deliveries)}"
            out.append(("deliveries", title, "tree", deliveries))
        if self._error:
            out.append(("error", None, "text", self._error))
        return out

    @staticmethod
    def _delivery(d: Any) -> dict:
        dead = d.state == "dead_letter"
        when = d.created_at.strftime("%m-%d %H:%M")
        detail = " · ".join(
            p
            for p in (f"attempt {d.attempt}", when, one_line(d.last_error or "", 50))
            if p
        )
        replay = f"side:drive:replay:{d.delivery_id}"
        return {
            "label": f"{d.state} · {d.reason}",
            "icon": "⚠" if dead else "·",
            "tone": "error" if dead else None,
            "detail": detail,
            "value": replay if dead else "side:none",
            "children": [{"label": "replay", "value": replay}] if dead else None,
        }

    # -- drives ---------------------------------------------------------

    def _service(self) -> Any:
        return drive_service(self.shell.engine)

    async def load_drive(self) -> None:
        actor = ActorRef("user", "local")
        try:
            view = await self._service().get_drive(
                self.ids[0], actor=actor, is_privileged=True
            )
            if view is None:
                self._error, self._drive = "this drive no longer exists", None
            else:
                self._view, self._drive = view, project_view(view)
                self._progress = list(
                    await self._service().list_drive_progress(
                        self.ids[0], actor=actor, is_privileged=True
                    )
                )
                self._deliveries = list(
                    await self._service().list_drive_deliveries(
                        self.ids[0], actor=actor, is_privileged=True
                    )
                )
                self._error = ""
        except Exception as exc:
            self._error = one_line(str(exc), 80)
        self.shell.chrome.side.refresh()

    async def drive_action(self, action_id: str) -> None:
        row, view = self._drive, self._view
        if row is None or view is None:
            return
        actor = ActorRef("user", "local")
        service = self._service()
        record = view.record
        try:
            match action_id:
                case "progress":
                    summary = await self.shell.panel(
                        Prompt(
                            question="Progress to record", placeholder="what happened"
                        )
                    )
                    if not summary or not str(summary).strip():
                        return
                    await service.report_drive_progress(
                        record.drive_id,
                        summary=str(summary).strip(),
                        evidence=None,
                        actor=actor,
                        is_privileged=True,
                    )
                case "assign":
                    items = [
                        {"label": e.view.name, "value": cid}
                        for cid, e in self.shell.entries.items()
                    ]
                    picked = await self.shell.panel(
                        Picker(items=items, title="Assign the drive to")
                    )
                    if not picked:
                        return
                    entry = self.shell.entries[str(picked)]
                    await service.assign_drive(
                        record.drive_id,
                        str(picked),
                        entry.creature.graph_id,
                        expected_revision=record.revision,
                        actor=actor,
                        is_privileged=True,
                    )
                case "unassign":
                    await service.unassign_drive(
                        record.drive_id,
                        expected_revision=record.revision,
                        actor=actor,
                        is_privileged=True,
                    )
                case replay if replay.startswith("replay:"):
                    await service.replay_drive_delivery(
                        replay.removeprefix("replay:"),
                        actor=actor,
                        is_privileged=True,
                        graph_id=getattr(record, "scope_id", None)
                        or self.shell.focused.creature.graph_id,
                    )
                case "wake":
                    await service.wake_drive(
                        record.drive_id,
                        actor=actor,
                        expected_revision=record.revision,
                        is_privileged=True,
                    )
                case _:
                    target = {
                        "pause": "paused",
                        "resume": "active",
                        "cancel": "cancelled",
                    }
                    await service.transition_drive(
                        record.drive_id,
                        DriveStatus(target[action_id]),
                        expected_revision=record.revision,
                        actor=actor,
                        is_privileged=True,
                    )
            self.shell.app.notify(f"drive: {action_id} done", "success")
        except Exception as exc:
            self.shell.app.notify(f"drive: {one_line(str(exc), 80)}", "error")
        await self.load_drive()

    # -- actions --------------------------------------------------------

    def act(self, what: str) -> None:
        """An action list entry (``side:<what>``) of this detail."""
        shell = self.shell
        if self.kind in ("trace", "job") and what in ("stop", "background"):
            cid, job_id = self.ids
            entry = shell.entries.get(cid)
            if entry is None:
                return
            name = job_id
            job = entry.view.jobs.get(job_id)
            if job is not None:
                name = job.name
            if what == "stop":
                entry.agent._cancel_job(job_id, name)
                shell.app.notify(f"stopping {name}", "info")
            elif not entry.agent._promote_handle(job_id):
                shell.app.notify("this job cannot move to the background", "warning")
        elif self.kind == "drive" and what.startswith("drive:"):
            shell.spawn(self.drive_action(what.removeprefix("drive:")))
