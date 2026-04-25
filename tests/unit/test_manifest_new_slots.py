"""Tests for Cluster 1 manifest slots — skills, commands, user_commands, prompts.

Covers:
- ``resolve_package_skills`` returns entries for a fixture package.
- ``resolve_package_command`` preserves the ``override: true`` flag.
- ``resolve_package_user_command`` same shape as commands.
- ``resolve_package_prompt`` returns an absolute file path.
- Cross-package collisions hard-error for commands / user_commands /
  prompts (skills too, at bulk enumeration) — matches decisions §1.1.
- ``list_packages()`` surfaces the new fields in its dict output.
- A ``{% include "<fragment>" %}`` in a string template resolves
  through the package manifest's ``prompts:`` slot.
"""

import sys

import pytest
import yaml

from kohakuterrarium.packages import install_package, list_packages
from kohakuterrarium.packages_manifest import (
    list_package_commands,
    list_package_prompts,
    list_package_skills,
    list_package_user_commands,
    resolve_package_command,
    resolve_package_prompt,
    resolve_package_skills,
    resolve_package_user_command,
)
from kohakuterrarium.prompt.template import render_template

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_packages(tmp_path, monkeypatch):
    """Redirect the package install root to a throwaway directory."""
    import kohakuterrarium.packages as pkg_mod

    monkeypatch.setattr(pkg_mod, "PACKAGES_DIR", tmp_path / "packages")
    (tmp_path / "packages").mkdir()
    return tmp_path / "packages"


def _make_manifest_package(
    root,
    *,
    pkg_name: str,
    skills: list[dict] | None = None,
    commands: list[dict] | None = None,
    user_commands: list[dict] | None = None,
    prompts: list[dict] | None = None,
    prompt_files: dict[str, str] | None = None,
):
    """Build a package directory with whichever manifest slots are passed."""
    pkg = root / pkg_name
    pkg.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"name": pkg_name, "version": "0.1.0"}
    if skills is not None:
        manifest["skills"] = skills
    if commands is not None:
        manifest["commands"] = commands
    if user_commands is not None:
        manifest["user_commands"] = user_commands
    if prompts is not None:
        manifest["prompts"] = prompts

    (pkg / "kohaku.yaml").write_text(yaml.dump(manifest))

    if prompt_files:
        for rel, content in prompt_files.items():
            fp = pkg / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")

    return pkg


@pytest.fixture(autouse=True)
def _scrub_template_cache():
    """Ensure template-loader state does not leak between tests.

    The Jinja environment inside ``prompt.template`` caches resolved
    templates — clear it between tests so the per-test packages-dir
    monkeypatch takes effect.
    """
    from kohakuterrarium.prompt import template as tmpl_mod

    tmpl_mod._env.cache.clear() if tmpl_mod._env.cache else None
    yield
    tmpl_mod._env.cache.clear() if tmpl_mod._env.cache else None


# ---------------------------------------------------------------------------
# resolve_package_skills + list_packages exposure
# ---------------------------------------------------------------------------


class TestSkillsSlot:
    def test_resolve_skills_single_package(self, tmp_path, tmp_packages):
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="skill-pkg",
            skills=[
                {
                    "name": "git-workflow",
                    "path": "skills/git-workflow",
                    "description": "Opinionated git workflow",
                },
                {
                    "name": "code-review",
                    "path": "skills/code-review",
                    "description": "Review checklist",
                },
            ],
        )
        install_package(str(pkg))

        entries = resolve_package_skills("skill-pkg")
        assert entries is not None
        names = {e["name"] for e in entries}
        assert names == {"git-workflow", "code-review"}

    def test_resolve_skills_missing_package(self, tmp_packages):
        assert resolve_package_skills("no-such-pkg") is None

    def test_list_packages_exposes_skills_field(self, tmp_path, tmp_packages):
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="skill-pkg2",
            skills=[{"name": "s1", "path": "skills/s1"}],
        )
        install_package(str(pkg))

        listed = list_packages()
        matching = next(p for p in listed if p["name"] == "skill-pkg2")
        assert matching["skills"] == [{"name": "s1", "path": "skills/s1"}]
        # All four new fields present (empty defaults where unused).
        for field in ("commands", "user_commands", "prompts", "templates"):
            assert field in matching

    def test_list_package_skills_collision_raises(self, tmp_path, tmp_packages):
        pkg_a = _make_manifest_package(
            tmp_path,
            pkg_name="skill-dup-a",
            skills=[{"name": "shared", "path": "skills/shared"}],
        )
        pkg_b = _make_manifest_package(
            tmp_path,
            pkg_name="skill-dup-b",
            skills=[{"name": "shared", "path": "skills/shared"}],
        )
        install_package(str(pkg_a))
        install_package(str(pkg_b))

        with pytest.raises(ValueError) as exc:
            list_package_skills()
        assert "shared" in str(exc.value)
        assert "skill-dup-a" in str(exc.value)
        assert "skill-dup-b" in str(exc.value)


# ---------------------------------------------------------------------------
# resolve_package_command
# ---------------------------------------------------------------------------


class TestCommandsSlot:
    def test_resolve_command_preserves_override_flag(self, tmp_path, tmp_packages):
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="cmd-pkg",
            commands=[
                {
                    "name": "info",
                    "module": "cmd_pkg.info_cmd",
                    "class": "InfoOverride",
                    "override": True,
                    "description": "Replace built-in info command.",
                }
            ],
        )
        install_package(str(pkg))

        entry = resolve_package_command("info")
        assert entry is not None
        assert entry["override"] is True
        assert entry["module"] == "cmd_pkg.info_cmd"

    def test_resolve_command_missing(self, tmp_packages):
        assert resolve_package_command("never_declared") is None

    def test_command_collision_raises(self, tmp_path, tmp_packages):
        pkg_a = _make_manifest_package(
            tmp_path,
            pkg_name="cmd-a",
            commands=[{"name": "shared", "module": "a", "class": "Shared"}],
        )
        pkg_b = _make_manifest_package(
            tmp_path,
            pkg_name="cmd-b",
            commands=[{"name": "shared", "module": "b", "class": "Shared"}],
        )
        install_package(str(pkg_a))
        install_package(str(pkg_b))

        with pytest.raises(ValueError) as exc:
            resolve_package_command("shared")
        msg = str(exc.value)
        assert "cmd-a" in msg and "cmd-b" in msg

    def test_list_package_commands_aggregates(self, tmp_path, tmp_packages):
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="cmd-list",
            commands=[
                {"name": "alpha", "module": "m", "class": "A"},
                {"name": "beta", "module": "m", "class": "B"},
            ],
        )
        install_package(str(pkg))
        out = list_package_commands()
        assert set(out) == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# resolve_package_user_command
# ---------------------------------------------------------------------------


class TestUserCommandsSlot:
    def test_resolve_user_command(self, tmp_path, tmp_packages):
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="uc-pkg",
            user_commands=[
                {"name": "hello", "module": "uc_pkg.hello", "class": "HelloCmd"}
            ],
        )
        install_package(str(pkg))
        entry = resolve_package_user_command("hello")
        assert entry is not None
        assert entry["class"] == "HelloCmd"

    def test_user_command_collision(self, tmp_path, tmp_packages):
        pkg_a = _make_manifest_package(
            tmp_path,
            pkg_name="uc-a",
            user_commands=[{"name": "shared", "module": "a", "class": "A"}],
        )
        pkg_b = _make_manifest_package(
            tmp_path,
            pkg_name="uc-b",
            user_commands=[{"name": "shared", "module": "b", "class": "B"}],
        )
        install_package(str(pkg_a))
        install_package(str(pkg_b))
        with pytest.raises(ValueError):
            resolve_package_user_command("shared")

    def test_list_package_user_commands(self, tmp_path, tmp_packages):
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="uc-list",
            user_commands=[{"name": "only", "module": "m", "class": "Only"}],
        )
        install_package(str(pkg))
        out = list_package_user_commands()
        assert "only" in out


# ---------------------------------------------------------------------------
# resolve_package_prompt + Jinja include
# ---------------------------------------------------------------------------


class TestPromptsSlot:
    def test_resolve_prompt_returns_absolute_path(self, tmp_path, tmp_packages):
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="prompt-pkg",
            prompts=[{"name": "git-rules", "path": "prompts/git-rules.md"}],
            prompt_files={"prompts/git-rules.md": "Never force push.\n"},
        )
        install_package(str(pkg))
        path = resolve_package_prompt("git-rules")
        assert path is not None
        assert path.is_absolute()
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "Never force push.\n"

    def test_resolve_prompt_missing(self, tmp_packages):
        assert resolve_package_prompt("never-declared") is None

    def test_templates_alias_resolves(self, tmp_path, tmp_packages):
        """The manifest accepts ``templates:`` as an alias for ``prompts:``."""
        pkg = tmp_path / "alias-pkg"
        pkg.mkdir()
        (pkg / "kohaku.yaml").write_text(
            yaml.dump(
                {
                    "name": "alias-pkg",
                    "version": "0.1",
                    "templates": [
                        {"name": "alias-frag", "path": "frags/alias.md"},
                    ],
                }
            )
        )
        (pkg / "frags").mkdir()
        (pkg / "frags" / "alias.md").write_text("aliased content")
        install_package(str(pkg))

        path = resolve_package_prompt("alias-frag")
        assert path is not None
        assert path.read_text(encoding="utf-8") == "aliased content"

    def test_prompt_collision_raises(self, tmp_path, tmp_packages):
        pkg_a = _make_manifest_package(
            tmp_path,
            pkg_name="p-a",
            prompts=[{"name": "shared", "path": "p/shared.md"}],
            prompt_files={"p/shared.md": "A"},
        )
        pkg_b = _make_manifest_package(
            tmp_path,
            pkg_name="p-b",
            prompts=[{"name": "shared", "path": "p/shared.md"}],
            prompt_files={"p/shared.md": "B"},
        )
        install_package(str(pkg_a))
        install_package(str(pkg_b))

        with pytest.raises(ValueError):
            resolve_package_prompt("shared")

    def test_list_package_prompts(self, tmp_path, tmp_packages):
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="plist",
            prompts=[
                {"name": "one", "path": "p/one.md"},
                {"name": "two", "path": "p/two.md"},
            ],
            prompt_files={"p/one.md": "1", "p/two.md": "2"},
        )
        install_package(str(pkg))
        out = list_package_prompts()
        assert set(out) == {"one", "two"}
        for p in out.values():
            assert p.is_absolute()

    def test_jinja_include_resolves_via_manifest(self, tmp_path, tmp_packages):
        """End-to-end: a creature-style ``{% include %}`` finds the fragment."""
        pkg = _make_manifest_package(
            tmp_path,
            pkg_name="include-pkg",
            prompts=[{"name": "safety-rules", "path": "prompts/safety.md"}],
            prompt_files={"prompts/safety.md": "DO NOT force push.\n"},
        )
        install_package(str(pkg))

        template = (
            "# Agent\n" 'Follow the house rules:\n{% include "safety-rules" %}\nEnd.\n'
        )
        rendered = render_template(template)
        assert "DO NOT force push." in rendered
        assert "{% include" not in rendered


# ---------------------------------------------------------------------------
# Backward compat — empty/missing slots never break list_packages()
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_list_packages_for_manifest_without_new_slots(self, tmp_path, tmp_packages):
        pkg = tmp_path / "legacy"
        pkg.mkdir()
        (pkg / "kohaku.yaml").write_text(
            yaml.dump({"name": "legacy", "version": "0.0.1"})
        )
        install_package(str(pkg))
        listed = list_packages()
        legacy = next(p for p in listed if p["name"] == "legacy")
        # All new fields default to empty list.
        assert legacy["skills"] == []
        assert legacy["commands"] == []
        assert legacy["user_commands"] == []
        assert legacy["prompts"] == []


# ---------------------------------------------------------------------------
# sys.modules cleanup so each test starts clean.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _scrub_sys_modules():
    before = set(sys.modules)
    yield
    after = set(sys.modules) - before
    for name in after:
        if name.startswith(("skill_pkg", "cmd_pkg", "uc_pkg", "include_pkg")):
            sys.modules.pop(name, None)
