"""Inspect and explicitly repair local image references before starting a session."""

import hashlib
import json
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.core.conversation.continuation import load_persisted_branch_view
from kohakuterrarium.core.conversation.media import (
    MissingHistoryImage,
    inspect_history_images,
    replace_missing_images,
)
from kohakuterrarium.session.history.replay import (
    index_parent_paths,
    resolve_selected_branches,
)
from kohakuterrarium.session.resume.branch import prepare_saved_snapshot
from kohakuterrarium.session.resume.checkpoint import verified_text_checkpoint
from kohakuterrarium.session.resume.open import open_store_with_migration
from kohakuterrarium.session.resume.resume import final_resume_history
from kohakuterrarium.session.resume.target import resolve_resume_path
from kohakuterrarium.session.store import SessionStore

MissingImagesPolicy = Literal["ignore", "warn", "error"]


@dataclass(frozen=True)
class ResumeImageIssue:
    """An unavailable image reference in a resumed creature's history."""

    namespace: str
    image: MissingHistoryImage


@dataclass(frozen=True)
class ResumeImageReport:
    """Unavailable image references and an optional repair confirmation."""

    issues: tuple[ResumeImageIssue, ...]
    confirmation: str | None = None

    def summary(self) -> str:
        files = sum(issue.image.kind == "file" for issue in self.issues)
        repairable = sum(issue.image.repairable for issue in self.issues)
        total = len(self.issues)
        return (
            f"{total} unavailable historical image reference(s): {files} original "
            f"file(s), {total - files} session artifact(s); {repairable} replaceable."
        )

    def detail(self, *, max_examples: int = 5) -> dict:
        return {
            "code": "missing_resume_images",
            "message": self.summary(),
            "total": len(self.issues),
            "files": sum(issue.image.kind == "file" for issue in self.issues),
            "artifacts": sum(issue.image.kind == "artifact" for issue in self.issues),
            "repairable": sum(issue.image.repairable for issue in self.issues),
            "confirmation": self.confirmation,
            "examples": [
                {"namespace": issue.namespace, **asdict(issue.image)}
                for issue in self.issues[:max_examples]
            ],
        }


class MissingResumeImagesError(SessionNotResumableError):
    """Resume paused because historical local images are unavailable."""

    def __init__(self, report: ResumeImageReport) -> None:
        self.report = report
        super().__init__(report.summary())


class MissingResumeImagesWarning(UserWarning):
    """Unavailable image references were left unchanged during resume."""

    def __init__(self, report: ResumeImageReport) -> None:
        self.report = report
        super().__init__(report.summary())


@dataclass
class _HistoryRepair:
    namespace: str
    branch: dict
    compact_state: dict | None
    messages: list[dict]
    issues: list[MissingHistoryImage]
    watermark: int


def _namespaces(store: SessionStore) -> list[str]:
    meta = store.load_meta()
    manifest = meta.get("live_graph_manifest")
    if isinstance(manifest, dict):
        return [item["name"] for item in manifest["creatures"]]
    return list(meta.get("agents") or [])


def _inspect_store(
    store: SessionStore,
) -> tuple[ResumeImageReport, list[_HistoryRepair]]:
    issues = []
    plans = []
    for namespace in _namespaces(store):
        if verified_text_checkpoint(store, namespace) is not None:
            continue
        events = store.get_events(namespace)
        selected = resolve_selected_branches(
            events,
            index_parent_paths(events),
            load_persisted_branch_view(store, namespace),
        )
        tip = max(selected, default=0)
        prepared = prepare_saved_snapshot(store, namespace, events)
        messages = (
            final_resume_history(store, namespace, events, prepared, selected) or []
        )
        found = inspect_history_images(
            messages,
            events,
            selected,
            tip,
            session_path=store.path,
            include_completed_tip=True,
        )
        issues.extend(ResumeImageIssue(namespace, item) for item in found)
        if any(item.repairable for item in found):
            plans.append(
                _HistoryRepair(
                    namespace,
                    {
                        "turn_index": tip,
                        "branch_id": selected[tip],
                        "parent_branch_path": sorted(
                            (t, b) for t, b in selected.items() if 0 < t < tip
                        ),
                        "context_cutoff_event_id": (
                            prepared.get("context_cutoff_event_id", 0)
                            if prepared
                            else 0
                        ),
                    },
                    prepared.get("compact_state") if prepared else None,
                    messages,
                    found,
                    max(
                        (
                            event.get("event_id", 0)
                            for event in events
                            if type(event.get("event_id")) is int
                        ),
                        default=0,
                    ),
                )
            )
    token = None
    if plans:
        payload = json.dumps(
            [store.meta.get("conversation_id"), [asdict(plan) for plan in plans]],
            sort_keys=True,
        )
        token = hashlib.sha256(payload.encode()).hexdigest()[:24]
    return ResumeImageReport(tuple(issues), token), plans


def inspect_resume_images(path: str | Path) -> ResumeImageReport:
    """Inspect the saved live conversations without acquiring a writer."""
    store = SessionStore.open_readonly(resolve_resume_path(Path(path)))
    try:
        return _inspect_store(store)[0]
    finally:
        store.close()


def apply_resume_image_policy(
    source: SessionStore | str | Path,
    policy: MissingImagesPolicy,
    *,
    confirmation: str | None = None,
) -> None:
    """Enforce resume policy, or persist a confirmed repair under the writer lock."""
    if policy not in ("ignore", "warn", "error"):
        raise ValueError("missing_images must be 'ignore', 'warn', or 'error'")
    if policy == "ignore" and confirmation is None:
        return
    if isinstance(source, SessionStore):
        _apply_store_policy(source, policy, confirmation)
    elif confirmation is None:
        _handle_issues(inspect_resume_images(source), policy)
    else:
        store = open_store_with_migration(resolve_resume_path(source), writer_lock=True)
        try:
            _apply_store_policy(store, policy, confirmation)
        finally:
            store.close(update_status=False)


def _apply_store_policy(
    store: SessionStore, policy: MissingImagesPolicy, confirmation: str | None
) -> None:
    report, plans = _inspect_store(store)
    if not report.issues:
        return
    if confirmation is None:
        _handle_issues(report, policy)
        return
    if confirmation != report.confirmation or any(
        not issue.image.repairable for issue in report.issues
    ):
        raise MissingResumeImagesError(report)
    for plan in plans:
        store.save_branch_snapshot(
            plan.namespace,
            replace_missing_images(plan.messages, plan.issues),
            branch=plan.branch,
            watermark=plan.watermark,
            compact_state=plan.compact_state,
            preserve_other_paths=True,
        )
    store.flush()


def _handle_issues(report: ResumeImageReport, policy: MissingImagesPolicy) -> None:
    if report.issues:
        if policy == "error":
            raise MissingResumeImagesError(report)
        warnings.warn(MissingResumeImagesWarning(report), stacklevel=3)
