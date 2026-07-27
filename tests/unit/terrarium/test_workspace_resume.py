from dataclasses import replace

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.graph_manifest import (
    GraphManifest,
    ManifestCreature,
    save_manifest,
)
from kohakuterrarium.terrarium.workspace_resume import (
    WorkspaceResumeError,
    WorkspaceResumeFailure,
    WorkspaceStatus,
    plan_workspace_resume,
    preflight_session_workspaces,
    preflight_workspace_resume,
)


def _creature(creature_id: str, pwd: str) -> ManifestCreature:
    return ManifestCreature(
        creature_id=creature_id,
        name=creature_id,
        config_snapshot={"name": creature_id},
        source_ref=None,
        pwd=pwd,
        is_privileged=False,
        parent_creature_id=None,
    )


def _manifest(*creatures: ManifestCreature) -> GraphManifest:
    return GraphManifest(
        graph_id="graph",
        creatures=creatures,
        channels=(),
        listen=(),
        send=(),
    )


def test_preflight_classifies_and_groups_shared_invalid_paths(tmp_path):
    valid = tmp_path / "valid"
    valid.mkdir()
    missing = tmp_path / "gone"
    manifest = _manifest(
        _creature("valid", str(valid)),
        _creature("first", str(missing)),
        _creature("second", str(missing)),
        _creature("no-pwd", ""),
    )

    result = preflight_workspace_resume(manifest)

    assert [(member.creature_id, member.status) for member in result.members] == [
        ("valid", WorkspaceStatus.VALID),
        ("first", WorkspaceStatus.INVALID),
        ("second", WorkspaceStatus.INVALID),
        ("no-pwd", WorkspaceStatus.MISSING),
    ]
    assert [(gap.pwd, gap.creature_ids) for gap in result.gaps] == [
        (str(missing), ("first", "second")),
        (None, ("no-pwd",)),
    ]
    assert result.gaps[0].target.startswith("path:")
    assert result.gaps[1].target == "creature:no-pwd"


def test_plan_replaces_path_group_and_preserves_valid_member(tmp_path):
    valid = tmp_path / "valid"
    replacement = tmp_path / "replacement"
    valid.mkdir()
    replacement.mkdir()
    missing = str(tmp_path / "missing")
    manifest = _manifest(
        _creature("valid", str(valid)),
        _creature("first", missing),
        _creature("second", missing),
    )
    group_target = preflight_workspace_resume(manifest).gaps[0].target

    plan = plan_workspace_resume(manifest, {group_target: str(replacement)})

    assert plan.working_dirs == {
        "valid": str(valid),
        "first": str(replacement),
        "second": str(replacement),
    }
    assert [creature.pwd for creature in plan.manifest.creatures] == [
        str(valid),
        str(replacement),
        str(replacement),
    ]
    assert manifest.creatures[1].pwd == missing
    assert plan.manifest is not manifest


def test_plan_can_replace_each_invalid_member(tmp_path):
    replacement_a = tmp_path / "a"
    replacement_b = tmp_path / "b"
    replacement_a.mkdir()
    replacement_b.mkdir()
    missing = str(tmp_path / "shared-missing")
    manifest = _manifest(
        _creature("first", missing),
        _creature("second", missing),
    )

    plan = plan_workspace_resume(
        manifest,
        {"first": str(replacement_a), "second": str(replacement_b)},
    )

    assert plan.working_dirs == {
        "first": str(replacement_a),
        "second": str(replacement_b),
    }


def test_plan_accepts_manifest_with_no_gaps(tmp_path):
    valid = tmp_path / "valid"
    valid.mkdir()
    manifest = _manifest(_creature("valid", str(valid)))

    plan = plan_workspace_resume(manifest)

    assert plan.working_dirs == {"valid": str(valid)}
    assert plan.manifest == manifest
    assert plan.manifest is not manifest


@pytest.mark.parametrize(
    ("replacement_target", "expected_code"),
    [
        ("unknown", WorkspaceResumeFailure.UNKNOWN_TARGET),
        ("valid", WorkspaceResumeFailure.VALID_TARGET),
    ],
)
def test_plan_rejects_unknown_or_valid_target(
    tmp_path, replacement_target, expected_code
):
    valid = tmp_path / "valid"
    replacement = tmp_path / "replacement"
    valid.mkdir()
    replacement.mkdir()
    manifest = _manifest(_creature("valid", str(valid)))

    with pytest.raises(WorkspaceResumeError) as raised:
        plan_workspace_resume(manifest, {replacement_target: str(replacement)})

    assert raised.value.code is expected_code
    assert raised.value.target == replacement_target


@pytest.mark.parametrize("replacement", ["", "not-created"])
def test_plan_rejects_invalid_replacement_directory(tmp_path, replacement):
    manifest = _manifest(_creature("missing", str(tmp_path / "gone")))

    with pytest.raises(WorkspaceResumeError) as raised:
        plan_workspace_resume(manifest, {"missing": replacement})

    assert raised.value.code is WorkspaceResumeFailure.INVALID_REPLACEMENT
    assert raised.value.target == "missing"
    assert raised.value.creature_ids == ("missing",)


def test_plan_rejects_file_as_replacement(tmp_path):
    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")
    manifest = _manifest(_creature("missing", str(tmp_path / "gone")))

    with pytest.raises(WorkspaceResumeError) as raised:
        plan_workspace_resume(manifest, {"missing": str(file_path)})

    assert raised.value.code is WorkspaceResumeFailure.INVALID_REPLACEMENT


def test_plan_fails_closed_when_any_gap_is_unresolved(tmp_path):
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    manifest = _manifest(
        _creature("first", str(tmp_path / "gone-a")),
        _creature("second", str(tmp_path / "gone-b")),
    )

    with pytest.raises(WorkspaceResumeError) as raised:
        plan_workspace_resume(manifest, {"first": str(replacement)})

    assert raised.value.code is WorkspaceResumeFailure.UNRESOLVED
    assert raised.value.creature_ids == ("second",)


def test_plan_rejects_conflicting_member_and_group_replacements(tmp_path):
    shared = str(tmp_path / "gone")
    first_replacement = tmp_path / "first"
    group_replacement = tmp_path / "group"
    first_replacement.mkdir()
    group_replacement.mkdir()
    manifest = _manifest(_creature("first", shared), _creature("second", shared))
    group_target = preflight_workspace_resume(manifest).gaps[0].target

    with pytest.raises(WorkspaceResumeError) as raised:
        plan_workspace_resume(
            manifest,
            {
                "first": str(first_replacement),
                group_target: str(group_replacement),
            },
        )

    assert raised.value.code is WorkspaceResumeFailure.CONFLICTING_REPLACEMENT
    assert raised.value.creature_ids == ("first",)


def test_dirty_session_fails_closed_during_read_only_preflight(tmp_path):
    path = tmp_path / "dirty.kohakutr"
    workdir = tmp_path / "work"
    workdir.mkdir()
    store = SessionStore(path)
    save_manifest(store, _manifest(_creature("c1", str(workdir))))
    store.meta["workspace_resume_state"] = {"status": "partial_dirty"}
    store.close(update_status=False)

    with pytest.raises(WorkspaceResumeError, match="incomplete workspace rollback"):
        preflight_session_workspaces(path)


def test_session_preflight_never_opens_writable_store(monkeypatch, tmp_path):
    path = tmp_path / "saved.kohakutr"
    workdir = tmp_path / "work"
    workdir.mkdir()
    store = SessionStore(path)
    save_manifest(store, _manifest(_creature("c1", str(workdir))))
    store.close(update_status=False)
    before_stat = path.stat()
    before_files = {item.name for item in tmp_path.iterdir()}
    monkeypatch.setattr(
        SessionStore,
        "open_readonly",
        lambda *args, **kwargs: pytest.fail("SessionStore must not be constructed"),
    )

    result = preflight_session_workspaces(path)

    assert result is not None and not result.gaps
    assert {item.name for item in tmp_path.iterdir()} == before_files
    after_stat = path.stat()
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_size,
        before_stat.st_mtime_ns,
    )


def test_preflight_does_not_mutate_manifest(tmp_path):
    manifest = _manifest(_creature("missing", str(tmp_path / "gone")))
    original = replace(manifest)

    preflight_workspace_resume(manifest)

    assert manifest == original
