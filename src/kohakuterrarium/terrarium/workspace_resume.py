"""Pure workspace validation and replacement planning for manifest resume."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.migrations import latest_readable_version
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.graph_manifest import (
    MANIFEST_KEY,
    GraphManifest,
    parse_manifest,
)


class WorkspaceStatus(str, Enum):
    """Filesystem status of a creature's saved working directory."""

    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"


class WorkspaceResumeFailure(str, Enum):
    """Stable reasons why a workspace resume plan was rejected."""

    UNKNOWN_TARGET = "unknown_target"
    VALID_TARGET = "valid_target"
    INVALID_REPLACEMENT = "invalid_replacement"
    UNRESOLVED = "unresolved"
    CONFLICTING_REPLACEMENT = "conflicting_replacement"
    STALE_MANIFEST = "stale_manifest"


class WorkspaceResumeError(SessionNotResumableError):
    """Structured, fail-closed workspace planning failure."""

    def __init__(
        self,
        code: WorkspaceResumeFailure,
        message: str,
        *,
        target: str | None = None,
        creature_ids: tuple[str, ...] = (),
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.target = target
        self.creature_ids = creature_ids
        self.path = path


@dataclass(frozen=True)
class WorkspaceMember:
    """One manifest member and the status of its saved workspace."""

    creature_id: str
    pwd: str | None
    status: WorkspaceStatus


@dataclass(frozen=True)
class WorkspaceGap:
    """A replacement target shared by members with the same missing path."""

    target: str
    pwd: str | None
    creature_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkspacePreflight:
    """Filesystem-only assessment of all working directories in a manifest."""

    revision: int
    members: tuple[WorkspaceMember, ...]
    gaps: tuple[WorkspaceGap, ...]

    @property
    def requires_replacement(self) -> bool:
        return bool(self.gaps)


@dataclass(frozen=True)
class WorkspaceResumePlan:
    """Resolved working directories and a safe manifest copy for persistence."""

    working_dirs: Mapping[str, str]
    manifest: GraphManifest


def preflight_to_dict(preflight: WorkspacePreflight) -> dict[str, Any]:
    """Serialize workspace preflight details for API and Lab transports."""
    return {
        "revision": preflight.revision,
        "ready": not preflight.requires_replacement,
        "members": [
            {
                "creature_id": member.creature_id,
                "saved_pwd": member.pwd,
                "status": member.status.value,
            }
            for member in preflight.members
        ],
        "gaps": [
            {
                "gap_id": gap.target,
                "saved_pwd": gap.pwd,
                "status": (
                    WorkspaceStatus.MISSING.value
                    if gap.pwd is None
                    else WorkspaceStatus.INVALID.value
                ),
                "creature_ids": list(gap.creature_ids),
            }
            for gap in preflight.gaps
        ],
    }


def preflight_session_workspaces(
    store: SessionStore | str | Path,
) -> WorkspacePreflight | None:
    """Read saved workspace metadata without migration or lifecycle writes."""
    path = Path(store.path if isinstance(store, SessionStore) else store)
    readable = latest_readable_version(path)
    meta = read_session_meta(readable)
    dirty_state = meta.get("workspace_resume_state")
    if isinstance(dirty_state, dict) and dirty_state.get("status") == "partial_dirty":
        raise WorkspaceResumeError(
            WorkspaceResumeFailure.STALE_MANIFEST,
            "Session has an incomplete workspace rollback",
        )
    raw_manifest = meta.get(MANIFEST_KEY)
    if raw_manifest is None:
        return None
    return preflight_workspace_resume(parse_manifest(raw_manifest))


def preflight_workspace_resume(manifest: GraphManifest) -> WorkspacePreflight:
    """Inspect every saved creature pwd without starting or writing anything."""
    members: list[WorkspaceMember] = []
    grouped: dict[str, list[str]] = {}
    missing_ids: list[str] = []

    for creature in manifest.creatures:
        pwd = creature.pwd if isinstance(creature.pwd, str) and creature.pwd else None
        status = _workspace_status(pwd)
        members.append(WorkspaceMember(creature.creature_id, pwd, status))
        if status is WorkspaceStatus.INVALID:
            assert pwd is not None
            grouped.setdefault(_path_key(pwd), []).append(creature.creature_id)
        elif status is WorkspaceStatus.MISSING:
            missing_ids.append(creature.creature_id)

    gaps = [
        WorkspaceGap(f"path:{key}", _saved_pwd(manifest, ids[0]), tuple(ids))
        for key, ids in grouped.items()
    ]
    gaps.extend(
        WorkspaceGap(f"creature:{creature_id}", None, (creature_id,))
        for creature_id in missing_ids
    )
    return WorkspacePreflight(manifest.revision, tuple(members), tuple(gaps))


def plan_workspace_resume(
    manifest: GraphManifest,
    replacements: Mapping[str, str] | None = None,
    *,
    allow_valid_targets: bool = False,
) -> WorkspaceResumePlan:
    """Validate replacements and return an all-resolved, immutable plan.

    Replacement keys may be an invalid member's creature id or a gap target from
    :func:`preflight_workspace_resume`. A grouped path target updates every
    member that shared that invalid saved path.
    """
    preflight = preflight_workspace_resume(manifest)
    requested = dict(replacements or {})
    member_by_id = {member.creature_id: member for member in preflight.members}
    gap_by_target = {gap.target: gap for gap in preflight.gaps}
    assignments: dict[str, str] = {}

    for target, replacement in requested.items():
        creature_ids = _replacement_targets(
            target,
            member_by_id,
            gap_by_target,
            allow_valid_targets=allow_valid_targets,
        )
        _validate_replacement(target, replacement, creature_ids)
        for creature_id in creature_ids:
            previous = assignments.get(creature_id)
            if previous is not None and not _same_path(previous, replacement):
                raise WorkspaceResumeError(
                    WorkspaceResumeFailure.CONFLICTING_REPLACEMENT,
                    f"Conflicting replacements for creature {creature_id!r}",
                    target=target,
                    creature_ids=(creature_id,),
                    path=replacement,
                )
            assignments[creature_id] = replacement

    unresolved = tuple(
        member.creature_id
        for member in preflight.members
        if member.status is not WorkspaceStatus.VALID
        and member.creature_id not in assignments
    )
    if unresolved:
        raise WorkspaceResumeError(
            WorkspaceResumeFailure.UNRESOLVED,
            f"Workspace replacements are required for: {', '.join(unresolved)}",
            creature_ids=unresolved,
        )

    working_dirs = {
        member.creature_id: assignments.get(member.creature_id, member.pwd or "")
        for member in preflight.members
    }
    updated_creatures = tuple(
        replace(creature, pwd=working_dirs[creature.creature_id])
        for creature in manifest.creatures
    )
    return WorkspaceResumePlan(
        working_dirs=working_dirs,
        manifest=replace(manifest, creatures=updated_creatures),
    )


def _workspace_status(pwd: str | None) -> WorkspaceStatus:
    if pwd is None:
        return WorkspaceStatus.MISSING
    return WorkspaceStatus.VALID if Path(pwd).is_dir() else WorkspaceStatus.INVALID


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _same_path(left: str, right: str) -> bool:
    return _path_key(left) == _path_key(right)


def _saved_pwd(manifest: GraphManifest, creature_id: str) -> str:
    return next(c.pwd for c in manifest.creatures if c.creature_id == creature_id)


def _replacement_targets(
    target: str,
    members: Mapping[str, WorkspaceMember],
    gaps: Mapping[str, WorkspaceGap],
    *,
    allow_valid_targets: bool,
) -> tuple[str, ...]:
    gap = gaps.get(target)
    if gap is not None:
        return gap.creature_ids
    member = members.get(target)
    if member is None:
        raise WorkspaceResumeError(
            WorkspaceResumeFailure.UNKNOWN_TARGET,
            f"Unknown workspace replacement target {target!r}",
            target=target,
        )
    if member.status is WorkspaceStatus.VALID and not allow_valid_targets:
        raise WorkspaceResumeError(
            WorkspaceResumeFailure.VALID_TARGET,
            f"Cannot replace valid workspace for creature {target!r}",
            target=target,
            creature_ids=(target,),
            path=member.pwd,
        )
    return (target,)


def _validate_replacement(
    target: str, replacement: object, creature_ids: tuple[str, ...]
) -> None:
    if (
        not isinstance(replacement, str)
        or not replacement
        or not Path(replacement).is_dir()
    ):
        raise WorkspaceResumeError(
            WorkspaceResumeFailure.INVALID_REPLACEMENT,
            f"Replacement for {target!r} is not an existing directory",
            target=target,
            creature_ids=creature_ids,
            path=replacement if isinstance(replacement, str) else None,
        )
