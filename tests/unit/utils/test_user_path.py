"""User PATH editing: pure string rules plus the registry and profile adapters."""

import os
import sys
from pathlib import Path

import pytest

from kohakuterrarium.utils import user_path

SEP = os.pathsep


class TestEntries:
    def test_adds_once_and_keeps_order(self):
        value = SEP.join(["a", "b"])
        added = user_path.with_entry(value, "c")
        assert added == SEP.join(["a", "b", "c"])
        assert user_path.with_entry(added, "c") == added

    def test_membership_ignores_case_and_separators_on_this_platform(self):
        value = SEP.join([str(Path("x") / "bin"), "y"])
        assert user_path.contains(value, str(Path("x") / "bin"))
        assert not user_path.contains(value, "z")

    def test_removal_drops_only_the_named_entry(self):
        value = SEP.join(["a", "b", "c"])
        assert user_path.without_entry(value, "b") == SEP.join(["a", "c"])
        assert user_path.without_entry(value, "q") == value

    def test_empty_segments_are_discarded(self):
        assert user_path.split_entries(SEP + "a" + SEP + SEP + "b") == ["a", "b"]


class TestProfileBlock:
    def test_block_is_appended_to_existing_text(self):
        text = user_path.profile_with_block("export A=1\n", "/opt/kt/bin")
        assert text.startswith("export A=1\n")
        assert 'export PATH="/opt/kt/bin:$PATH"' in text
        assert text.count(user_path.BLOCK_START) == 1

    def test_reinstalling_replaces_the_block_instead_of_stacking(self):
        once = user_path.profile_with_block("export A=1\n", "/old")
        twice = user_path.profile_with_block(once, "/new")
        assert twice.count(user_path.BLOCK_START) == 1
        assert "/old" not in twice and "/new" in twice

    def test_block_in_an_empty_profile(self):
        text = user_path.profile_with_block("", "/d")
        assert text == user_path.profile_block("/d")

    def test_removal_restores_the_original_text(self):
        original = "export A=1\n"
        installed = user_path.profile_with_block(original, "/d")
        assert user_path.profile_without_block(installed).strip() == original.strip()

    def test_removal_without_a_block_is_a_no_op(self):
        assert user_path.profile_without_block("export A=1\n") == "export A=1\n"

    def test_unterminated_block_is_left_alone(self):
        broken = f"{user_path.BLOCK_START}\nexport PATH=x\n"
        assert user_path.profile_without_block(broken) == broken


class TestPosixProfile:
    @pytest.fixture
    def profile(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "linux")
        path = tmp_path / ".profile"
        monkeypatch.setattr(user_path, "profile_path", lambda: path)
        return path

    def test_add_then_detect_then_remove(self, profile, tmp_path):
        directory = tmp_path / "bin"
        profile.write_text("export KEEP=1\n", encoding="utf-8")

        assert user_path.is_on_user_path(directory) is False
        user_path.add_to_user_path(directory)
        assert user_path.is_on_user_path(directory) is True
        assert "export KEEP=1" in profile.read_text(encoding="utf-8")

        user_path.remove_from_user_path(directory)
        assert user_path.is_on_user_path(directory) is False
        assert profile.read_text(encoding="utf-8").strip() == "export KEEP=1"

    def test_missing_profile_is_created_and_removal_of_nothing_is_safe(
        self, profile, tmp_path
    ):
        user_path.remove_from_user_path(tmp_path / "bin")
        assert not profile.exists()
        user_path.add_to_user_path(tmp_path / "bin")
        assert profile.is_file()


class TestWindowsRegistry:
    @pytest.fixture
    def registry(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        store = {"Path": r"C:\Tools;C:\Other"}
        monkeypatch.setattr(user_path, "_read_registry_path", lambda: store["Path"])
        monkeypatch.setattr(
            user_path, "_write_registry_path", lambda value: store.update(Path=value)
        )
        return store

    def test_add_detect_remove_round_trip(self, registry):
        directory = Path(r"C:\Users\me\AppData\Local\KohakuTerrarium\bin")

        assert user_path.is_on_user_path(directory) is False
        user_path.add_to_user_path(directory)
        assert user_path.is_on_user_path(directory) is True
        assert registry["Path"].split(SEP)[:2] == [r"C:\Tools", r"C:\Other"]

        user_path.remove_from_user_path(directory)
        assert registry["Path"] == SEP.join([r"C:\Tools", r"C:\Other"])

    def test_adding_twice_does_not_duplicate(self, registry):
        directory = Path(r"C:\kt\bin")
        user_path.add_to_user_path(directory)
        user_path.add_to_user_path(directory)
        assert registry["Path"].count(r"C:\kt\bin") == 1
