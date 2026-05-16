"""Unit tests for :mod:`kohakuterrarium.packages.resolve`.

Covers ``@pkg/path`` reference resolution, sys.path injection, and the
collision-aware per-kind manifest scanners (tools / io / triggers).
"""

import sys

import pytest

from kohakuterrarium.packages import locations as loc_mod
from kohakuterrarium.packages import resolve as res_mod
from kohakuterrarium.packages.resolve import (
    ensure_package_importable,
    is_package_ref,
    resolve_package_io,
    resolve_package_path,
    resolve_package_tool,
    resolve_package_trigger,
)


@pytest.fixture
def pkg_dir(tmp_path, monkeypatch):
    d = tmp_path / "packages"
    d.mkdir()
    monkeypatch.setattr(loc_mod, "PACKAGES_DIR", d)
    return d


class TestIsPackageRef:
    def test_at_prefixed_string_is_ref(self):
        assert is_package_ref("@biome/creatures/swe") is True

    def test_plain_path_is_not_ref(self):
        assert is_package_ref("/abs/path") is False

    def test_non_string_is_not_ref(self):
        assert is_package_ref(None) is False


class TestResolvePackagePath:
    def test_non_at_reference_raises_value_error(self):
        with pytest.raises(ValueError, match="must start with @"):
            resolve_package_path("biome/creatures")

    def test_uninstalled_package_raises_file_not_found(self, pkg_dir):
        with pytest.raises(FileNotFoundError, match="Package not installed: ghost"):
            resolve_package_path("@ghost/anything")

    def test_resolves_subpath_inside_package(self, pkg_dir):
        pkg = pkg_dir / "biome"
        sub = pkg / "creatures" / "swe"
        sub.mkdir(parents=True)
        resolved = resolve_package_path("@biome/creatures/swe")
        assert resolved == sub.resolve()

    def test_resolves_package_root_when_no_subpath(self, pkg_dir):
        pkg = pkg_dir / "biome"
        pkg.mkdir()
        assert resolve_package_path("@biome") == pkg.resolve()

    def test_missing_subpath_raises_file_not_found(self, pkg_dir):
        (pkg_dir / "biome").mkdir()
        with pytest.raises(FileNotFoundError, match="Path not found"):
            resolve_package_path("@biome/missing/dir")


class TestEnsurePackageImportable:
    def test_missing_package_returns_false(self, pkg_dir):
        assert ensure_package_importable("not_installed") is False

    def test_adds_package_root_to_sys_path(self, pkg_dir, monkeypatch):
        pkg = pkg_dir / "imp"
        pkg.mkdir()
        # Work on a copy of sys.path so the test is reversible.
        original = list(sys.path)
        monkeypatch.setattr(sys, "path", original)
        added = ensure_package_importable("imp")
        assert added is True
        assert str(pkg.resolve()) in sys.path

    def test_idempotent_when_already_on_path(self, pkg_dir, monkeypatch):
        pkg = pkg_dir / "imp"
        pkg.mkdir()
        path_copy = list(sys.path) + [str(pkg.resolve())]
        monkeypatch.setattr(sys, "path", path_copy)
        before = list(sys.path)
        assert ensure_package_importable("imp") is True
        # Already present → no duplicate inserted.
        assert sys.path == before


def _patch_packages(monkeypatch, packages):
    monkeypatch.setattr(res_mod, "list_packages", lambda: packages)


class TestResolvePackageTool:
    def test_finds_tool_by_name(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "p", "tools": [{"name": "grep2", "module": "m", "class": "G"}]}],
        )
        assert resolve_package_tool("grep2") == ("m", "G")

    def test_accepts_class_name_alias(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [
                {
                    "name": "p",
                    "tools": [{"name": "t", "module": "m", "class_name": "T"}],
                }
            ],
        )
        assert resolve_package_tool("t") == ("m", "T")

    def test_unknown_tool_returns_none(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "p", "tools": []}])
        assert resolve_package_tool("ghost") is None

    def test_entry_missing_module_skipped(self, monkeypatch):
        _patch_packages(
            monkeypatch, [{"name": "p", "tools": [{"name": "t", "class": "T"}]}]
        )
        # No module → not a usable match → None.
        assert resolve_package_tool("t") is None

    def test_non_dict_entry_skipped(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "p", "tools": ["junk"]}])
        assert resolve_package_tool("t") is None


class TestResolvePackageIoAndTrigger:
    def test_io_resolved(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "p", "io": [{"name": "discord", "module": "m", "class": "D"}]}],
        )
        assert resolve_package_io("discord") == ("m", "D")

    def test_trigger_resolved(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [
                {
                    "name": "p",
                    "triggers": [{"name": "cron2", "module": "m", "class": "C"}],
                }
            ],
        )
        assert resolve_package_trigger("cron2") == ("m", "C")

    def test_no_match_returns_none(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "p", "io": []}])
        assert resolve_package_io("nope") is None

    def test_collision_across_packages_raises(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [
                {
                    "name": "pkg-a",
                    "io": [{"name": "discord", "module": "ma", "class": "A"}],
                },
                {
                    "name": "pkg-b",
                    "io": [{"name": "discord", "module": "mb", "class": "B"}],
                },
            ],
        )
        with pytest.raises(ValueError, match="Collision for io name 'discord'"):
            resolve_package_io("discord")
        # Both conflicting package names appear in the message.
        try:
            resolve_package_io("discord")
        except ValueError as e:
            assert "pkg-a" in str(e) and "pkg-b" in str(e)

    def test_non_dict_io_entry_skipped(self, monkeypatch):
        # A junk (non-dict) entry must not crash the scanner.
        _patch_packages(
            monkeypatch,
            [
                {
                    "name": "p",
                    "io": ["junk", {"name": "other", "module": "m", "class": "C"}],
                }
            ],
        )
        assert resolve_package_io("discord") is None

    def test_io_entry_missing_class_skipped(self, monkeypatch):
        _patch_packages(
            monkeypatch, [{"name": "p", "io": [{"name": "discord", "module": "m"}]}]
        )
        # No class → incomplete entry → treated as no match.
        assert resolve_package_io("discord") is None

    def test_duplicate_same_package_does_not_collide(self, monkeypatch):
        # Two entries, one usable one not — only one real match → no error.
        _patch_packages(
            monkeypatch,
            [
                {
                    "name": "p",
                    "triggers": [
                        {"name": "cron", "module": "m", "class": "C"},
                        {"name": "cron", "class": "C"},  # missing module, skipped
                    ],
                }
            ],
        )
        assert resolve_package_trigger("cron") == ("m", "C")
