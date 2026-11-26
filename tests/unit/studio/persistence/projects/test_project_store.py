"""Unit tests for ``studio.persistence.projects.store``."""

import json
import threading

import pytest

from kohakuterrarium.errors import ConflictError, ProjectsFileError
from kohakuterrarium.studio.persistence.projects import store as store_mod
from kohakuterrarium.studio.persistence.projects.paths import project_id
from kohakuterrarium.studio.persistence.projects.store import (
    load_document,
    load_records,
    projects_path,
    update_document,
)
from kohakuterrarium.utils.file.lock import FileLock


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _append(path: str, node=None, **extra):
    def mutate(document):
        document["projects"].append({"path": path, "node": node, **extra})

    return mutate


class TestPaths:
    def test_shared_file_honours_config_dir(self, config):
        assert projects_path() == config / "projects.json"

    def test_user_file_lives_under_users(self, config):
        assert projects_path(7) == config / "users" / "7" / "projects.json"


class TestLoad:
    def test_missing_file_is_an_empty_document(self, config):
        assert load_document() == {"version": 1, "projects": []}
        assert load_records() == []

    def test_record_id_is_recomputed_from_its_key(self, config):
        (config / "projects.json").write_text(
            json.dumps({"version": 1, "projects": [{"id": "stale", "path": "/a/"}]})
        )
        assert load_records()[0]["id"] == project_id("", "/a")

    @pytest.mark.parametrize(
        "content",
        [
            "{not json",
            "[]",
            json.dumps({"projects": {}}),
            json.dumps({"projects": [{"path": ""}]}),
            json.dumps({"projects": ["/a"]}),
            json.dumps({"projects": [{"path": "/a", "node": 3}]}),
        ],
    )
    def test_corrupt_file_raises_a_typed_error(self, config, content):
        (config / "projects.json").write_text(content)
        with pytest.raises(ProjectsFileError) as info:
            load_document()
        assert info.value.path == str(config / "projects.json")

    def test_unreadable_file_raises_a_typed_error(self, config):
        (config / "projects.json").mkdir()
        with pytest.raises(ProjectsFileError):
            load_document()


class TestUpdate:
    def test_write_round_trips_and_keeps_unknown_fields(self, config):
        (config / "projects.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "extra": {"keep": True},
                    "projects": [{"path": "/a", "node": None, "color": "red"}],
                }
            )
        )
        update_document(_append("/b"))
        saved = json.loads((config / "projects.json").read_text())
        assert saved["extra"] == {"keep": True}
        assert saved["version"] == 1
        assert [r["path"] for r in saved["projects"]] == ["/a", "/b"]
        assert saved["projects"][0]["color"] == "red"
        assert saved["projects"][1]["id"] == project_id("", "/b")

    def test_corrupt_file_is_never_overwritten(self, config):
        target = config / "projects.json"
        target.write_text("{broken")
        with pytest.raises(ProjectsFileError):
            update_document(_append("/b"))
        assert target.read_text() == "{broken"

    def test_failing_mutation_writes_nothing(self, config):
        update_document(_append("/a"))
        before = (config / "projects.json").read_text()

        def boom(document):
            document["projects"].clear()
            raise RuntimeError("stop")

        with pytest.raises(RuntimeError):
            update_document(boom)
        assert (config / "projects.json").read_text() == before

    def test_failed_replace_leaves_no_temp_file(self, config, monkeypatch):
        update_document(_append("/a"))

        def fail(*_args):
            raise OSError("disk full")

        monkeypatch.setattr(store_mod.os, "replace", fail)
        with pytest.raises(OSError):
            update_document(_append("/b"))
        assert sorted(p.name for p in config.iterdir()) == [
            "projects.json",
            "projects.json.lock",
        ]
        assert [r["path"] for r in load_records()] == ["/a"]

    def test_mutation_return_value_is_returned(self, config):
        assert update_document(lambda document: 42) == 42

    def test_users_do_not_share_a_file(self, config):
        update_document(_append("/alice"), user_id=1)
        update_document(_append("/bob"), user_id=2)
        assert [r["path"] for r in load_records(1)] == ["/alice"]
        assert [r["path"] for r in load_records(2)] == ["/bob"]
        assert load_records() == []

    def test_concurrent_writers_lose_nothing(self, config):
        threads = [
            threading.Thread(target=update_document, args=(_append(f"/p{i}"),))
            for i in range(16)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(r["path"] for r in load_records()) == sorted(
            f"/p{i}" for i in range(16)
        )

    def test_lock_held_elsewhere_times_out_as_conflict(self, config, monkeypatch):
        monkeypatch.setattr(store_mod, "_LOCK_TIMEOUT_S", 0.05)
        holder = FileLock(config / "projects.json.lock")
        holder.acquire()
        try:
            with pytest.raises(ConflictError):
                update_document(_append("/a"))
        finally:
            holder.release()
        assert not (config / "projects.json").exists()
