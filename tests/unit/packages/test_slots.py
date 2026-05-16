"""Unit tests for :mod:`kohakuterrarium.packages.slots`.

Covers the cluster-1 manifest-slot resolvers — skills, commands,
user_commands, prompts/templates — including the cross-cutting
collision policy (hard error when two packages declare the same name).
"""

import pytest

from kohakuterrarium.packages import slots as slots_mod
from kohakuterrarium.packages.slots import (
    list_package_commands,
    list_package_prompts,
    list_package_skills,
    list_package_user_commands,
    resolve_package_command,
    resolve_package_prompt,
    resolve_package_skills,
    resolve_package_user_command,
)


def _patch_packages(monkeypatch, packages):
    monkeypatch.setattr(slots_mod, "list_packages", lambda: packages)


class TestResolvePackageSkills:
    def test_returns_skill_entries_for_named_package(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "biome", "skills": [{"name": "git", "path": "skills/git"}]}],
        )
        skills = resolve_package_skills("biome")
        assert skills == [{"name": "git", "path": "skills/git"}]

    def test_installed_package_no_skills_returns_empty_list(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "biome"}])
        # Installed but skill-less → [] (distinct from None = not installed).
        assert resolve_package_skills("biome") == []

    def test_uninstalled_package_returns_none(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "other"}])
        assert resolve_package_skills("ghost") is None

    def test_non_dict_skill_entries_filtered(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "p", "skills": ["junk", {"name": "real", "path": "x"}]}],
        )
        assert resolve_package_skills("p") == [{"name": "real", "path": "x"}]


class TestListPackageSkills:
    def test_aggregates_across_packages(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [
                {"name": "a", "skills": [{"name": "s1", "path": "p1"}]},
                {"name": "b", "skills": [{"name": "s2", "path": "p2"}]},
            ],
        )
        result = list_package_skills()
        assert set(result) == {"s1", "s2"}
        assert result["s1"]["path"] == "p1"

    def test_collision_raises_value_error(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [
                {"name": "a", "skills": [{"name": "dup", "path": "p1"}]},
                {"name": "b", "skills": [{"name": "dup", "path": "p2"}]},
            ],
        )
        with pytest.raises(ValueError, match="Collision for skills name 'dup'"):
            list_package_skills()

    def test_unnamed_entry_skipped(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "a", "skills": [{"path": "no-name"}]}])
        # Entry without a name can't be keyed → dropped, no error.
        assert list_package_skills() == {}


class TestResolvePackageCommand:
    def test_resolves_command_entry(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "p", "commands": [{"name": "deploy", "module": "m"}]}],
        )
        assert resolve_package_command("deploy") == {"name": "deploy", "module": "m"}

    def test_unknown_command_returns_none(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "p", "commands": []}])
        assert resolve_package_command("nope") is None

    def test_non_dict_command_entry_skipped(self, monkeypatch):
        # _scan_manifest_field must skip junk entries without crashing.
        _patch_packages(
            monkeypatch,
            [{"name": "p", "commands": ["junk", {"name": "deploy", "module": "m"}]}],
        )
        assert resolve_package_command("deploy") == {"name": "deploy", "module": "m"}

    def test_list_commands_skips_non_dict_entry(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "p", "commands": ["junk", {"name": "real"}]}],
        )
        assert set(list_package_commands()) == {"real"}

    def test_collision_raises(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [
                {"name": "a", "commands": [{"name": "x", "module": "ma"}]},
                {"name": "b", "commands": [{"name": "x", "module": "mb"}]},
            ],
        )
        with pytest.raises(ValueError, match="Collision for commands name 'x'"):
            resolve_package_command("x")

    def test_list_package_commands_aggregates(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "p", "commands": [{"name": "c1"}, {"name": "c2"}]}],
        )
        assert set(list_package_commands()) == {"c1", "c2"}


class TestResolvePackageUserCommand:
    def test_resolves_slash_command(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "p", "user_commands": [{"name": "review", "module": "m"}]}],
        )
        assert resolve_package_user_command("review") == {
            "name": "review",
            "module": "m",
        }

    def test_unknown_returns_none(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "p", "user_commands": []}])
        assert resolve_package_user_command("nope") is None

    def test_list_user_commands_aggregates(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "p", "user_commands": [{"name": "u1"}]}],
        )
        assert set(list_package_user_commands()) == {"u1"}


class TestResolvePackagePrompt:
    def test_resolves_fragment_to_absolute_path(self, tmp_path, monkeypatch):
        pkg_root = tmp_path / "pkg"
        frag = pkg_root / "prompts" / "git.md"
        frag.parent.mkdir(parents=True)
        frag.write_text("git rules")
        _patch_packages(
            monkeypatch,
            [
                {
                    "name": "pkg",
                    "prompts": [{"name": "git-safety", "path": "prompts/git.md"}],
                }
            ],
        )
        monkeypatch.setattr(slots_mod, "get_package_root", lambda n: pkg_root)
        resolved = resolve_package_prompt("git-safety")
        assert resolved == frag.resolve()

    def test_templates_alias_accepted(self, tmp_path, monkeypatch):
        pkg_root = tmp_path / "pkg"
        frag = pkg_root / "t.md"
        pkg_root.mkdir()
        frag.write_text("x")
        _patch_packages(
            monkeypatch,
            [{"name": "pkg", "templates": [{"name": "tmpl", "path": "t.md"}]}],
        )
        monkeypatch.setattr(slots_mod, "get_package_root", lambda n: pkg_root)
        assert resolve_package_prompt("tmpl") == frag.resolve()

    def test_unknown_fragment_returns_none(self, monkeypatch):
        _patch_packages(monkeypatch, [{"name": "p", "prompts": []}])
        assert resolve_package_prompt("nope") is None

    def test_entry_without_path_skipped(self, monkeypatch):
        _patch_packages(
            monkeypatch, [{"name": "p", "prompts": [{"name": "git-safety"}]}]
        )
        # No path field → logged + skipped → None.
        assert resolve_package_prompt("git-safety") is None

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        pkg_root = tmp_path / "pkg"
        pkg_root.mkdir()
        _patch_packages(
            monkeypatch,
            [{"name": "pkg", "prompts": [{"name": "f", "path": "gone.md"}]}],
        )
        monkeypatch.setattr(slots_mod, "get_package_root", lambda n: pkg_root)
        # Declared path points at a non-existent file → None.
        assert resolve_package_prompt("f") is None

    def test_unresolvable_package_root_returns_none(self, monkeypatch):
        _patch_packages(
            monkeypatch,
            [{"name": "pkg", "prompts": [{"name": "f", "path": "x.md"}]}],
        )
        monkeypatch.setattr(slots_mod, "get_package_root", lambda n: None)
        assert resolve_package_prompt("f") is None


class TestListPackagePrompts:
    def test_aggregates_resolved_paths(self, tmp_path, monkeypatch):
        pkg_root = tmp_path / "pkg"
        f1 = pkg_root / "a.md"
        f2 = pkg_root / "b.md"
        pkg_root.mkdir()
        f1.write_text("a")
        f2.write_text("b")
        _patch_packages(
            monkeypatch,
            [
                {
                    "name": "pkg",
                    "prompts": [
                        {"name": "a", "path": "a.md"},
                        {"name": "b", "path": "b.md"},
                    ],
                }
            ],
        )
        monkeypatch.setattr(slots_mod, "get_package_root", lambda n: pkg_root)
        result = list_package_prompts()
        assert result == {"a": f1.resolve(), "b": f2.resolve()}

    def test_missing_file_dropped_from_result(self, tmp_path, monkeypatch):
        pkg_root = tmp_path / "pkg"
        good = pkg_root / "good.md"
        pkg_root.mkdir()
        good.write_text("g")
        _patch_packages(
            monkeypatch,
            [
                {
                    "name": "pkg",
                    "prompts": [
                        {"name": "good", "path": "good.md"},
                        {"name": "bad", "path": "missing.md"},
                    ],
                }
            ],
        )
        monkeypatch.setattr(slots_mod, "get_package_root", lambda n: pkg_root)
        result = list_package_prompts()
        # The missing-file fragment is silently dropped, the good one stays.
        assert result == {"good": good.resolve()}

    def test_cross_key_collision_raises(self, tmp_path, monkeypatch):
        pkg_root = tmp_path / "pkg"
        pkg_root.mkdir()
        (pkg_root / "x.md").write_text("x")
        # Same name "shared" declared under prompts: in one pkg and
        # templates: in another.
        _patch_packages(
            monkeypatch,
            [
                {"name": "a", "prompts": [{"name": "shared", "path": "x.md"}]},
                {"name": "b", "templates": [{"name": "shared", "path": "x.md"}]},
            ],
        )
        monkeypatch.setattr(slots_mod, "get_package_root", lambda n: pkg_root)
        with pytest.raises(ValueError, match="prompt fragment 'shared'"):
            list_package_prompts()
