"""Resume image reporting, policy, and branch-local snapshot repair."""

import warnings

import pytest

from kohakuterrarium.session.resume.images import (
    MissingResumeImagesError,
    MissingResumeImagesWarning,
    apply_resume_image_policy,
    inspect_resume_images,
)
from kohakuterrarium.session.store import SessionStore


def image_part(url):
    return {"type": "image_url", "image_url": {"url": url}}


def write_turn(store, content, turn=1, *, complete=True):
    store.append_event(
        "alice",
        "user_message",
        {"content": content},
        turn_index=turn,
        branch_id=1,
        parent_branch_path=[(previous, 1) for previous in range(1, turn)],
    )
    store.append_event("alice", "processing_start", {}, turn_index=turn, branch_id=1)
    if complete:
        store.append_event("alice", "processing_end", {}, turn_index=turn, branch_id=1)


@pytest.fixture
def saved(tmp_path):
    path = tmp_path / "photos.kohakutr"
    original = tmp_path / "gone.png"
    urls = [original.as_uri(), "/api/sessions/photos/artifacts/tool_outputs/gone.png"]
    store = SessionStore(path)
    try:
        store.init_meta("photos", "agent", "", str(tmp_path), ["alice"])
        write_turn(store, [image_part(url) for url in urls])
    finally:
        store.close(update_status=False)
    return path, original


def test_preview_and_policies_do_not_replace_history(saved):
    path, _ = saved
    report = inspect_resume_images(path)
    assert report.detail()["files"] == 1
    assert report.detail()["artifacts"] == 1
    assert report.detail()["repairable"] == 2
    assert report.confirmation
    with pytest.raises(MissingResumeImagesError) as rejected:
        apply_resume_image_policy(path, "error")
    assert rejected.value.report == report
    with pytest.warns(MissingResumeImagesWarning) as warned:
        apply_resume_image_policy(path, "warn")
    assert warned[0].message.report == report
    with warnings.catch_warnings(record=True) as caught:
        apply_resume_image_policy(path, "ignore")
    assert not caught
    assert inspect_resume_images(path) == report


def test_confirm_rechecks_and_saves_only_replacements(saved):
    path, original = saved
    old_report = inspect_resume_images(path)
    original.write_bytes(b"restored")
    with pytest.raises(MissingResumeImagesError) as stale:
        apply_resume_image_policy(path, "error", confirmation=old_report.confirmation)
    assert stale.value.report.detail()["artifacts"] == 1
    assert stale.value.report.confirmation != old_report.confirmation
    apply_resume_image_policy(
        path, "error", confirmation=stale.value.report.confirmation
    )
    assert not inspect_resume_images(path).issues
    store = SessionStore.open_readonly(path)
    try:
        messages = store.load_conversation("alice")
        assert messages[0]["content"][0] == image_part(original.as_uri())
        assert "Historical image unavailable" in messages[0]["content"][1]["text"]
        assert store.get_events("alice")[0]["content"][1]["type"] == "image_url"
    finally:
        store.close()


def test_repair_checkpoint_write_preserves_its_descendant(saved):
    path, _ = saved
    descendant = [{"role": "assistant", "content": "Only in the descendant snapshot"}]
    store = SessionStore(path)
    try:
        write_turn(store, "later", 2)
        store.save_branch_snapshot(
            "alice",
            descendant,
            branch={"turn_index": 2, "branch_id": 1, "parent_branch_path": [(1, 1)]},
            watermark=store.max_event_id("alice"),
        )
        store.save_branch_snapshot(
            "alice",
            [{"role": "assistant", "content": "Repaired ancestor"}],
            branch={"turn_index": 1, "branch_id": 1, "parent_branch_path": []},
            watermark=store.max_event_id("alice"),
            preserve_other_paths=True,
        )
    finally:
        store.close(update_status=False)
    store = SessionStore.open_readonly(path)
    try:
        assert (
            store.load_branch_snapshot("alice", {1: 1, 2: 1})["messages"] == descendant
        )
        assert store.load_branch_snapshot("alice", {1: 1})["messages"] == [
            {"role": "assistant", "content": "Repaired ancestor"}
        ]
    finally:
        store.close()


def test_restored_files_make_confirmation_a_noop(saved):
    path, original = saved
    report = inspect_resume_images(path)
    original.write_bytes(b"restored")
    artifact = path.with_suffix(".artifacts") / "tool_outputs" / "gone.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"restored")
    apply_resume_image_policy(path, "error", confirmation=report.confirmation)
    assert not inspect_resume_images(path).issues
    store = SessionStore.open_readonly(path)
    try:
        assert store.load_conversation("alice") is None
    finally:
        store.close()


def test_unfinished_turn_is_reported_but_protected(saved, tmp_path):
    path, _ = saved
    store = SessionStore(path)
    try:
        write_turn(
            store, [image_part((tmp_path / "pending.png").as_uri())], 2, complete=False
        )
    finally:
        store.close(update_status=False)
    report = inspect_resume_images(path)
    assert report.detail()["total"] == 3
    assert report.detail()["repairable"] == 2
    assert not report.issues[-1].image.repairable
    with pytest.raises(MissingResumeImagesError):
        apply_resume_image_policy(path, "error", confirmation=report.confirmation)
    assert inspect_resume_images(path) == report
