"""Integration test for the ``packages/`` package.

This is the comprehensive USAGE EXAMPLE of the kt package system. Each
method runs a COMPLETE workflow end-to-end — never a granular
per-method check. The only seam is the packages directory: it is
redirected to a per-test ``tmp_path`` by monkeypatching
``packages.locations.PACKAGES_DIR`` (the documented test hook —
``_packages_dir()`` consults it live). Everything else is a production
collaborator: real bundle directories written to ``tmp_path`` as the
install source, the real ``install_package`` copytree / ``.link``
writer, the real ``kohaku.yaml`` manifest loader, the real
``list_packages`` enumerator, and the real ``resolve_package_path``
``@pkg/path`` resolver.

The workflows mirror how the codebase actually drives ``packages/``:

- ``cli/packages.py`` → ``studio/catalog/packages.py`` runs the
  ``kt install`` / ``kt list`` / ``kt info`` / ``kt uninstall``
  lifecycle: ``install_package_op`` → ``list_installed_packages`` →
  ``load_agent_info`` / ``get_package_modules`` → ``uninstall_package_op``.
- ``core/config.py`` (``_resolve_base_config``) and
  ``terrarium/tools_group_lifecycle.py`` resolve ``@pkg/creatures/<name>``
  references in recipes via ``is_package_ref`` + ``resolve_package_path``.
- ``packages/resolve.py`` scanners (``resolve_package_tool`` etc.) are
  what ``bootstrap`` uses to find package-declared tools / io / triggers.

So the tests drive install → list → info → resolve → re-install →
uninstall the SAME way a real ``kt install`` user (and the recipe
loader) goes through it.
"""

import os
from pathlib import Path

import pytest
import yaml

from kohakuterrarium.packages import locations
from kohakuterrarium.packages.install import install_package, uninstall_package
from kohakuterrarium.packages.locations import (
    find_package_root_for_path,
    get_package_path,
    get_package_root,
    read_link,
)
from kohakuterrarium.packages.manifest import (
    _load_manifest,
    get_package_framework_hints,
)
from kohakuterrarium.packages.resolve import (
    ensure_package_importable,
    is_package_ref,
    resolve_package_io,
    resolve_package_path,
    resolve_package_tool,
    resolve_package_trigger,
)
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
from kohakuterrarium.packages.walk import get_package_modules, list_packages
from kohakuterrarium.studio.catalog import packages as studio_packages

pytestmark = pytest.mark.timeout(30)


# ── bundle builders — real on-disk package sources ───────────────


def _write(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_creature_bundle(
    root: Path,
    pkg_name: str,
    *,
    creature_name: str = "swe",
    version: str = "1.0.0",
) -> Path:
    """Build a real creature-bundle source dir, return its path.

    Mirrors the ``kt-biome`` layout: a ``kohaku.yaml`` manifest at the
    root with a ``creatures:`` list, and a ``creatures/<name>/config.yaml``
    on disk. Deliberately carries NO ``python_dependencies`` /
    ``requirements.txt`` so install never shells out to ``pip``.
    """
    src = root / f"{pkg_name}-src"
    manifest = {
        "name": pkg_name,
        "version": version,
        "description": f"{pkg_name} test bundle",
        "creatures": [
            {
                "name": creature_name,
                "path": f"creatures/{creature_name}",
                "description": "test creature",
            }
        ],
        "tools": [
            {
                "name": f"{pkg_name}_tool",
                "module": f"{pkg_name}.tools.thing",
                "class": "ThingTool",
                "description": "a package-declared tool",
            }
        ],
    }
    _write(src / "kohaku.yaml", yaml.safe_dump(manifest, sort_keys=False))
    _write(
        src / "creatures" / creature_name / "config.yaml",
        yaml.safe_dump(
            {
                "name": creature_name,
                "description": "test creature",
                "model": "test-model",
            },
            sort_keys=False,
        ),
    )
    _write(
        src / "creatures" / creature_name / "system.md",
        f"You are the {creature_name} creature from {pkg_name}.\n",
    )
    return src


def _make_terrarium_bundle(root: Path, pkg_name: str) -> Path:
    """Build a real terrarium-bundle source dir, return its path."""
    src = root / f"{pkg_name}-src"
    manifest = {
        "name": pkg_name,
        "version": "2.1.0",
        "description": f"{pkg_name} terrarium bundle",
        "terrariums": [
            {
                "name": "duo",
                "path": "terrariums/duo",
                "description": "two-creature recipe",
            }
        ],
    }
    _write(src / "kohaku.yaml", yaml.safe_dump(manifest, sort_keys=False))
    _write(
        src / "terrariums" / "duo" / "terrarium.yaml",
        yaml.safe_dump({"name": "duo", "creatures": []}, sort_keys=False),
    )
    return src


def _make_extension_bundle(
    root: Path,
    pkg_name: str,
    *,
    io_name: str,
    trigger_name: str,
    slot_suffix: str = "",
) -> Path:
    """Build an extension-only bundle: a manifest declaring ``io:`` and
    ``triggers:`` entries plus ``framework_hints``, with NO creatures/ or
    terrariums/ dir. This exercises the manifest-slot resolvers
    (``resolve_package_io`` / ``resolve_package_trigger``) and the
    ``_validate_package`` "extension modules only" path.

    When ``slot_suffix`` is given, the manifest ALSO declares the four
    cluster-1 manifest slots (``skills`` / ``commands`` / ``user_commands``
    / ``prompts``) so the ``packages.slots`` resolvers have real targets;
    the suffix keeps entry names distinct so two such bundles don't
    collide.
    """
    src = root / f"{pkg_name}-src"
    manifest = {
        "name": pkg_name,
        "version": "0.1.0",
        "description": f"{pkg_name} extension bundle",
        "framework_hints": {"tool_call_format": "xml", "max_turns": 12},
        "io": [
            {
                "name": io_name,
                "module": f"{pkg_name}.io.thing",
                "class": "ThingIO",
            }
        ],
        "triggers": [
            {
                "name": trigger_name,
                "module": f"{pkg_name}.triggers.thing",
                "class_name": "ThingTrigger",
            }
        ],
    }
    if slot_suffix:
        manifest["skills"] = [
            {"name": f"skill_{slot_suffix}", "path": f"skills/{slot_suffix}.md"}
        ]
        manifest["commands"] = [
            {
                "name": f"cmd_{slot_suffix}",
                "module": f"{pkg_name}.commands.thing",
                "class": "ThingCommand",
            }
        ]
        manifest["user_commands"] = [
            {
                "name": f"slash_{slot_suffix}",
                "module": f"{pkg_name}.usercmds.thing",
                "class_name": "ThingSlash",
            }
        ]
        manifest["prompts"] = [
            {"name": f"frag_{slot_suffix}", "path": f"prompts/{slot_suffix}.md"}
        ]
        # The prompt fragment must exist on disk — resolve_package_prompt
        # drops a fragment whose file is missing.
        _write(
            src / "prompts" / f"{slot_suffix}.md",
            f"Shared prompt fragment {slot_suffix}.\n",
        )
    _write(src / "kohaku.yaml", yaml.safe_dump(manifest, sort_keys=False))
    return src


def _make_bare_bundle(root: Path, pkg_name: str) -> Path:
    """A bundle with NO creatures/, NO terrariums/, NO extension modules
    — exercises the ``_validate_package`` warning path. Uses the
    ``kohaku.yml`` (alternate extension) manifest filename."""
    src = root / f"{pkg_name}-src"
    _write(
        src / "kohaku.yml",
        yaml.safe_dump({"name": pkg_name, "version": "0.0.1"}, sort_keys=False),
    )
    return src


# ── fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def packages_dir(tmp_path, monkeypatch):
    """Redirect the packages dir to a per-test tmpdir.

    ``packages.locations.PACKAGES_DIR`` is the documented test hook —
    ``_packages_dir()`` reads it live, so ``install_package`` /
    ``list_packages`` / ``resolve_package_path`` all land here. The
    studio-tier module captured the constant by value at import time,
    so patch it there too for the ``kt``-CLI workflow.
    """
    target = tmp_path / "kt-packages"
    monkeypatch.setattr(locations, "PACKAGES_DIR", target)
    monkeypatch.setattr(studio_packages, "PACKAGES_DIR", target, raising=False)
    return target


@pytest.fixture
def bundle_root(tmp_path):
    """A scratch dir holding bundle *sources* (kept separate from installs)."""
    root = tmp_path / "sources"
    root.mkdir()
    return root


# ── workflows ────────────────────────────────────────────────────


class TestPackagesIntegration:
    """End-to-end usage workflows for the kt package system.

    Each method is one fat workflow run start-to-finish: build a real
    bundle on disk, install it, observe it through every read path the
    codebase uses, then tear it down — asserting the actual filesystem
    state and manifest content at each step, never a shape.
    """

    def test_install_list_info_resolve_uninstall_lifecycle(
        self, packages_dir, bundle_root
    ):
        """The full ``kt install`` → ``list`` → ``info`` → ``resolve`` →
        install-2nd → ``uninstall`` → ``list`` lifecycle.

        This is the exact path a user takes with the ``kt`` CLI, driven
        through the same ``studio/catalog/packages.py`` ops the CLI
        calls, cross-checked against the low-tier library.
        """
        # ---- before any install: the packages dir does not exist yet,
        # so every enumerate path is empty (not an error). ----
        assert not packages_dir.exists()
        assert list_packages() == []
        assert studio_packages.list_installed_packages() == []
        assert get_package_root("alpha") is None

        # ---- build a real creature bundle source on disk ----
        src = _make_creature_bundle(bundle_root, "alpha", creature_name="swe")
        assert (src / "kohaku.yaml").is_file()

        # ---- kt install <local-path> ----
        name = studio_packages.install_package_op(str(src))
        assert name == "alpha"
        installed = packages_dir / "alpha"
        # A non-editable install is a real copytree, not a pointer.
        assert installed.is_dir()
        assert (installed / "kohaku.yaml").is_file()
        assert (installed / "creatures" / "swe" / "config.yaml").is_file()
        assert (installed / "creatures" / "swe" / "system.md").read_text(
            encoding="utf-8"
        ) == "You are the swe creature from alpha.\n"
        # The copy is independent of the source: mutating the source
        # afterwards must not change the installed package.
        (src / "creatures" / "swe" / "system.md").write_text(
            "MUTATED SOURCE\n", encoding="utf-8"
        )
        assert (installed / "creatures" / "swe" / "system.md").read_text(
            encoding="utf-8"
        ) == "You are the swe creature from alpha.\n"

        # ---- kt list ----
        listed = studio_packages.list_installed_packages()
        assert [p["name"] for p in listed] == ["alpha"]
        alpha = listed[0]
        assert alpha["version"] == "1.0.0"
        assert alpha["editable"] is False
        assert Path(alpha["path"]) == installed.resolve()
        assert [c["name"] for c in alpha["creatures"]] == ["swe"]

        # ---- kt info: the manifest the codebase reads back ----
        manifest = _load_manifest(get_package_root("alpha"))
        assert manifest["name"] == "alpha"
        assert manifest["description"] == "alpha test bundle"
        assert manifest["creatures"][0]["path"] == "creatures/swe"
        # get_package_modules is what bootstrap uses to enumerate a slot.
        tools = get_package_modules("alpha", "tools")
        assert len(tools) == 1
        assert tools[0]["name"] == "alpha_tool"
        assert tools[0]["module"] == "alpha.tools.thing"

        # ---- resolve a @pkg/path reference (recipe loader path) ----
        ref = "@alpha/creatures/swe"
        assert is_package_ref(ref) is True
        assert is_package_ref("creatures/swe") is False
        resolved = resolve_package_path(ref)
        assert resolved == (installed / "creatures" / "swe").resolve()
        assert (resolved / "config.yaml").is_file()
        # Resolving the bare package root works too.
        assert resolve_package_path("@alpha") == installed.resolve()
        # A path that doesn't exist in the package is a hard error.
        with pytest.raises(FileNotFoundError):
            resolve_package_path("@alpha/creatures/nonexistent")
        # A ref that doesn't start with @ is a ValueError, not a miss.
        with pytest.raises(ValueError, match="must start with @"):
            resolve_package_path("alpha/creatures/swe")
        # The package-scanner resolver (bootstrap tool lookup) finds it.
        assert resolve_package_tool("alpha_tool") == (
            "alpha.tools.thing",
            "ThingTool",
        )
        # ...and returns None for a tool no installed package declares.
        assert resolve_package_tool("no_such_tool") is None

        # ---- get_package_path / get_package_modules edge paths ----
        # get_package_path is an alias for get_package_root.
        assert get_package_path("alpha") == installed.resolve()
        assert get_package_path("ghost") is None
        # get_package_modules on a missing package -> [] (not an error).
        assert get_package_modules("ghost", "tools") == []
        # ...and on a manifest slot the bundle didn't declare -> [].
        assert get_package_modules("alpha", "plugins") == []

        # ---- ensure_package_importable: adds the pkg root to sys.path ----
        import sys as _sys

        before = list(_sys.path)
        try:
            assert ensure_package_importable("alpha") is True
            assert str(installed.resolve()) in _sys.path
            # Idempotent: a second call still returns True, no duplicate.
            assert ensure_package_importable("alpha") is True
            assert _sys.path.count(str(installed.resolve())) == 1
            # A package that isn't installed -> False, sys.path untouched.
            assert ensure_package_importable("ghost") is False
        finally:
            _sys.path[:] = before

        # ---- find_package_root_for_path: walk up to the manifest ----
        # From a file deep inside the package, the resolver walks up to
        # the dir holding kohaku.yaml — this is how a creature whose
        # config lives in <pkg>/creatures/<n>/ finds its package root.
        deep_file = installed / "creatures" / "swe" / "config.yaml"
        assert find_package_root_for_path(deep_file) == installed.resolve()
        # From the package dir itself -> the package dir.
        assert find_package_root_for_path(installed) == installed.resolve()
        # None input and a path with no manifest ancestor -> None.
        assert find_package_root_for_path(None) is None
        assert find_package_root_for_path(bundle_root) is None

        # ---- get_package_framework_hints: the manifest's hints block ----
        # alpha's manifest carries no framework_hints -> empty dict.
        assert get_package_framework_hints(get_package_root("alpha")) == {}
        # None input -> empty dict (no manifest to read).
        assert get_package_framework_hints(None) == {}

        # ---- install a second package ----
        src2 = _make_terrarium_bundle(bundle_root, "beta")
        name2 = studio_packages.install_package_op(str(src2))
        assert name2 == "beta"
        assert {p["name"] for p in list_packages()} == {"alpha", "beta"}
        # @beta resolves independently of @alpha.
        assert (
            resolve_package_path("@beta/terrariums/duo")
            == (packages_dir / "beta" / "terrariums" / "duo").resolve()
        )

        # ---- an extension-only bundle: io/trigger manifest resolvers ----
        # Install via the low-level install_package (the install.py
        # entrypoint), not the studio op, to exercise that path too.
        ext_src = _make_extension_bundle(
            bundle_root,
            "ext1",
            io_name="discord_io",
            trigger_name="cron_trigger",
            slot_suffix="one",
        )
        assert install_package(str(ext_src)) == "ext1"
        # resolve_package_io / resolve_package_trigger scan installed
        # packages' manifest slots — bootstrap uses these to find
        # package-declared io / trigger classes.
        assert resolve_package_io("discord_io") == ("ext1.io.thing", "ThingIO")
        # ``class_name`` is accepted as an alias for ``class``.
        assert resolve_package_trigger("cron_trigger") == (
            "ext1.triggers.thing",
            "ThingTrigger",
        )
        # A name no package declares -> None.
        assert resolve_package_io("nonexistent_io") is None
        assert resolve_package_trigger("nonexistent_trigger") is None
        # framework_hints from this bundle's manifest are coerced to str.
        assert get_package_framework_hints(get_package_root("ext1")) == {
            "tool_call_format": "xml",
            "max_turns": "12",
        }

        # ---- packages.slots: the cluster-1 manifest-slot resolvers ----
        # ext1 declares skills / commands / user_commands / prompts —
        # these are how skill discovery + controller-command registration
        # + Jinja {% include %} find package-declared entries.
        assert resolve_package_skills("ext1") == [
            {"name": "skill_one", "path": "skills/one.md"}
        ]
        # An installed package with no skills slot -> empty list; a
        # package that isn't installed at all -> None.
        assert resolve_package_skills("alpha") == []
        assert resolve_package_skills("ghostpkg") is None
        assert list_package_skills() == {
            "skill_one": {"name": "skill_one", "path": "skills/one.md"}
        }
        # commands: resolved by name; ``class`` key.
        assert resolve_package_command("cmd_one") == {
            "name": "cmd_one",
            "module": "ext1.commands.thing",
            "class": "ThingCommand",
        }
        assert resolve_package_command("no_such_cmd") is None
        assert set(list_package_commands()) == {"cmd_one"}
        # user_commands: resolved by name; ``class_name`` alias key.
        assert resolve_package_user_command("slash_one") == {
            "name": "slash_one",
            "module": "ext1.usercmds.thing",
            "class_name": "ThingSlash",
        }
        assert resolve_package_user_command("no_such_slash") is None
        assert set(list_package_user_commands()) == {"slash_one"}
        # prompts: resolved to an absolute on-disk path under the install.
        frag_path = resolve_package_prompt("frag_one")
        assert frag_path == (get_package_root("ext1") / "prompts" / "one.md").resolve()
        assert frag_path.read_text(encoding="utf-8") == "Shared prompt fragment one.\n"
        assert resolve_package_prompt("no_such_frag") is None
        assert list_package_prompts() == {"frag_one": frag_path}

        # A SECOND package declaring the SAME io name -> collision is a
        # hard ValueError naming both packages (io / trigger name clashes
        # are load-time errors per the extension-point design).
        ext_src2 = _make_extension_bundle(
            bundle_root,
            "ext2",
            io_name="discord_io",
            trigger_name="other_trigger",
            slot_suffix="two",
        )
        assert install_package(str(ext_src2)) == "ext2"
        with pytest.raises(ValueError, match="Collision for io"):
            resolve_package_io("discord_io")
        # Distinct trigger names from the two packages still resolve fine.
        assert resolve_package_trigger("other_trigger") == (
            "ext2.triggers.thing",
            "ThingTrigger",
        )
        # With both ext1 + ext2 installed, the slot enumerators now see
        # BOTH packages' distinct entries merged into one dict.
        assert set(list_package_commands()) == {"cmd_one", "cmd_two"}
        assert set(list_package_skills()) == {"skill_one", "skill_two"}
        assert set(list_package_prompts()) == {"frag_one", "frag_two"}

        # ---- a THIRD package re-declaring ext1's command name -> the
        # slot collision policy hard-errors (single-name lookup AND bulk
        # enumeration), naming the conflicting packages.
        ext_src3 = bundle_root / "ext3-src"
        _write(
            ext_src3 / "kohaku.yaml",
            yaml.safe_dump(
                {
                    "name": "ext3",
                    "version": "0.1.0",
                    "commands": [
                        {
                            "name": "cmd_one",  # clashes with ext1
                            "module": "ext3.commands.thing",
                            "class": "ThingCommand",
                        }
                    ],
                },
                sort_keys=False,
            ),
        )
        assert install_package(str(ext_src3)) == "ext3"
        with pytest.raises(ValueError, match="Collision for commands"):
            resolve_package_command("cmd_one")
        with pytest.raises(ValueError, match="Collision for commands"):
            list_package_commands()
        uninstall_package("ext3")
        # With ext3 gone the collision clears.
        assert resolve_package_command("cmd_one")["module"] == "ext1.commands.thing"

        uninstall_package("ext1")
        uninstall_package("ext2")
        # All slot resolvers go quiet once the declaring packages are gone.
        assert list_package_skills() == {}
        assert resolve_package_command("cmd_one") is None
        assert resolve_package_prompt("frag_one") is None

        # ---- a bare bundle (no creatures/terrariums/extensions) ----
        # Install still succeeds (validation only WARNS); the manifest is
        # read from the alternate ``kohaku.yml`` filename.
        bare_src = _make_bare_bundle(bundle_root, "bare1")
        assert install_package(str(bare_src)) == "bare1"
        bare_manifest = _load_manifest(get_package_root("bare1"))
        assert bare_manifest["name"] == "bare1"
        assert bare_manifest["version"] == "0.0.1"
        # A package dir with NO manifest at all -> a synthetic
        # ``{"name": <dir>}`` manifest (the _load_manifest fallback).
        nomanifest_dir = packages_dir / "no_manifest_pkg"
        nomanifest_dir.mkdir(parents=True)
        assert _load_manifest(nomanifest_dir) == {"name": "no_manifest_pkg"}
        uninstall_package("bare1")
        nomanifest_dir.rmdir()

        # ---- kt uninstall alpha ----
        assert studio_packages.uninstall_package_op("alpha") is True
        assert not (packages_dir / "alpha").exists()
        # list now shows ONLY beta — alpha is gone, beta untouched.
        remaining = studio_packages.list_installed_packages()
        assert [p["name"] for p in remaining] == ["beta"]
        assert get_package_root("alpha") is None
        # @alpha references now fail; @beta still resolves.
        with pytest.raises(FileNotFoundError):
            resolve_package_path("@alpha/creatures/swe")
        assert resolve_package_path("@beta").is_dir()
        # Uninstalling something already gone returns False (not an error).
        assert studio_packages.uninstall_package_op("alpha") is False
        # beta survives a no-op uninstall of alpha.
        assert {p["name"] for p in list_packages()} == {"beta"}

    def test_editable_install_points_at_live_source(self, packages_dir, bundle_root):
        """``kt install -e <path>`` writes a ``.link`` pointer, not a copy.

        The editable path is what a package *author* uses: edits to the
        source must be visible through the resolver with no re-install.
        Mirrors ``install_package(..., editable=True)`` from
        ``studio.catalog.packages.install_package_op``.
        """
        src = _make_creature_bundle(bundle_root, "devpkg", creature_name="researcher")

        name = studio_packages.install_package_op(str(src), editable=True)
        assert name == "devpkg"
        # Editable install = a ``.link`` pointer file, NOT a copied dir.
        link_file = packages_dir / "devpkg.link"
        assert link_file.is_file()
        assert not (packages_dir / "devpkg").exists()
        assert read_link("devpkg") == src.resolve()
        # The resolver points straight at the live source tree.
        assert get_package_root("devpkg") == src.resolve()

        listed = list_packages()
        assert [p["name"] for p in listed] == ["devpkg"]
        assert listed[0]["editable"] is True
        assert Path(listed[0]["path"]) == src.resolve()

        # Edit the source — no re-install — and the change is live.
        sys_md = src / "creatures" / "researcher" / "system.md"
        sys_md.write_text("EDITED LIVE\n", encoding="utf-8")
        resolved = resolve_package_path("@devpkg/creatures/researcher")
        assert resolved == (src / "creatures" / "researcher").resolve()
        assert (resolved / "system.md").read_text(encoding="utf-8") == ("EDITED LIVE\n")

        # Uninstall removes the pointer; the source dir is untouched.
        assert uninstall_package("devpkg") is True
        assert not link_file.exists()
        assert src.is_dir()
        assert (src / "kohaku.yaml").is_file()
        assert get_package_root("devpkg") is None
        assert list_packages() == []

        # ---- install from a real git repo (the ``kt install <url>``
        # path). Build a local checkout whose directory name literally
        # ends in ``.git`` so ``install_package`` routes through
        # ``_install_from_git`` -> ``git clone``. ----
        import subprocess

        git_repo = bundle_root / "gitpkg.git"
        # Build the bundle content directly inside the .git-named dir.
        _write(
            git_repo / "kohaku.yaml",
            yaml.safe_dump(
                {"name": "gitpkg", "version": "1.0.0", "creatures": []},
                sort_keys=False,
            ),
        )
        (git_repo / "creatures").mkdir()
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        for cmd in (
            ["git", "init", "-q"],
            ["git", "add", "-A"],
            ["git", "commit", "-q", "-m", "init"],
        ):
            subprocess.run(cmd, cwd=git_repo, check=True, env=env, capture_output=True)

        # The ``.git`` suffix routes install_package to the clone branch;
        # the repo name is the URL's last path component minus ``.git``.
        cloned_name = install_package(str(git_repo))
        # On a POSIX URL the name would be the bare ``gitpkg``; the
        # derivation splits on ``/`` so a Windows path keeps more — what
        # matters is the package installed and is keyed by that name.
        assert cloned_name.endswith("gitpkg")
        installed_git = get_package_root(cloned_name)
        assert installed_git is not None
        assert (installed_git / "kohaku.yaml").is_file()
        assert (installed_git / ".git").exists()  # a real clone, not a copy
        # Re-installing the same git URL pulls in place (the "already
        # exists" branch) — still resolves to the same package.
        assert install_package(str(git_repo)) == cloned_name
        assert uninstall_package(cloned_name) is True
        assert get_package_root(cloned_name) is None

        # ---- a source that is neither a git URL nor a local dir is a
        # hard ValueError, not a silent miss. ----
        with pytest.raises(ValueError, match="Cannot install from"):
            install_package(str(bundle_root / "does-not-exist-anywhere"))

    def test_reinstall_replaces_and_switches_layout(self, packages_dir, bundle_root):
        """Re-installing over an existing package replaces it cleanly.

        Covers three replace scenarios the install code explicitly
        handles: copy-over-copy (content fully replaced, no stale
        files), editable-over-copy (``.link`` replaces the dir), and
        copy-over-editable (dir replaces the ``.link``). This is the
        ``kt install`` idempotency contract.
        """
        # ---- v1: copy install ----
        src_v1 = _make_creature_bundle(
            bundle_root, "rolling", creature_name="swe", version="1.0.0"
        )
        # Add a file that should NOT survive a replacing re-install.
        _write(src_v1 / "creatures" / "swe" / "stale.txt", "v1-only\n")
        studio_packages.install_package_op(str(src_v1))
        installed = packages_dir / "rolling"
        assert (installed / "creatures" / "swe" / "stale.txt").is_file()
        assert _load_manifest(installed)["version"] == "1.0.0"

        # ---- v2: copy-over-copy from a different source dir ----
        v2_root = bundle_root / "v2"
        v2_root.mkdir()
        src_v2 = _make_creature_bundle(
            v2_root, "rolling", creature_name="swe", version="3.0.0"
        )
        studio_packages.install_package_op(str(src_v2))
        # Version bumped, and the stale file from v1 is GONE — the old
        # tree was torn down, not merged.
        assert _load_manifest(installed)["version"] == "3.0.0"
        assert not (installed / "creatures" / "swe" / "stale.txt").exists()
        assert [p["version"] for p in list_packages()] == ["3.0.0"]

        # ---- editable-over-copy: .link replaces the copied dir ----
        studio_packages.install_package_op(str(src_v2), editable=True)
        assert not (packages_dir / "rolling").exists()
        assert (packages_dir / "rolling.link").is_file()
        assert get_package_root("rolling") == src_v2.resolve()
        listed = list_packages()
        assert len(listed) == 1
        assert listed[0]["editable"] is True

        # ---- copy-over-editable: dir replaces the .link ----
        studio_packages.install_package_op(str(src_v2), editable=False)
        assert (packages_dir / "rolling").is_dir()
        assert not (packages_dir / "rolling.link").exists()
        assert read_link("rolling") is None
        listed = list_packages()
        assert len(listed) == 1
        assert listed[0]["editable"] is False
        # Still resolvable after all the layout churn.
        assert resolve_package_path("@rolling/creatures/swe").is_dir()

    def test_name_override_install_is_listable_under_its_name(
        self, packages_dir, bundle_root
    ):
        """A ``kt install --name X`` install must be discoverable as X.

        ``install_package(name_override=...)`` is the ``--name`` CLI flag
        (``install_package_op(..., name=...)``). It writes the package to
        ``packages/<name_override>/`` and returns ``name_override``, so
        every other read path — ``get_package_root``, ``resolve_package_path``,
        ``uninstall_package`` — keys on that name.

        Regression guard for B-packages-1 (FIXED): ``list_packages`` was
        the odd one out — it reported the manifest's ``name:`` field, so
        a ``--name``'d package showed up under the WRONG name (a
        duplicate of the un-renamed install) and was invisible to
        ``kt list`` / ``kt update <alias>``. The fix reports the
        install-dir name as the canonical ``name`` (keeping the bundle's
        self-declared name as ``manifest_name`` for display).
        """
        src = _make_creature_bundle(bundle_root, "rolling", creature_name="swe")

        first = studio_packages.install_package_op(str(src))
        assert first == "rolling"
        alias = studio_packages.install_package_op(str(src), name="rolling-alias")
        assert alias == "rolling-alias"

        # On disk both installs exist as separate directories.
        assert (packages_dir / "rolling").is_dir()
        assert (packages_dir / "rolling-alias").is_dir()
        # Resolution by override name works (keys on the dir name)...
        assert resolve_package_path("@rolling-alias/creatures/swe").is_dir()
        assert (
            get_package_root("rolling-alias")
            == (packages_dir / "rolling-alias").resolve()
        )

        # ...but `kt list` should also surface it under "rolling-alias".
        # It does NOT — it reports two packages both named "rolling".
        assert {p["name"] for p in list_packages()} == {
            "rolling",
            "rolling-alias",
        }

    def test_legacy_symlink_install_layout(self, packages_dir, bundle_root):
        """A legacy bare-symlink package dir is still listed / resolved.

        Modern editable installs use a ``.link`` pointer file, but
        ``locations.get_package_root`` and ``walk.list_packages`` still
        carry an explicit branch for the *old* layout: a bare symlink
        under ``PACKAGES_DIR`` pointing at the real checkout. This
        exercises that branch end-to-end. On Windows dev/CI the symlink
        ``os.symlink`` raises ``WinError 1314`` without
        ``SeCreateSymbolicLinkPrivilege`` — skip rather than fail.
        """
        src = _make_creature_bundle(bundle_root, "legacy", creature_name="swe")
        packages_dir.mkdir(parents=True, exist_ok=True)
        link = packages_dir / "legacy"
        try:
            link.symlink_to(src, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("symlink creation needs SeCreateSymbolicLinkPrivilege")
            raise

        # list_packages flags the symlinked package as editable.
        listed = list_packages()
        assert [p["name"] for p in listed] == ["legacy"]
        assert listed[0]["editable"] is True
        assert Path(listed[0]["path"]) == src.resolve()

        # get_package_root follows the symlink to the real checkout.
        assert get_package_root("legacy") == src.resolve()
        # @pkg references resolve through the symlink.
        resolved = resolve_package_path("@legacy/creatures/swe")
        assert resolved == (src / "creatures" / "swe").resolve()
        assert (resolved / "config.yaml").is_file()

        # uninstall removes the symlink, leaving the real checkout intact.
        assert uninstall_package("legacy") is True
        assert not link.exists()
        assert src.is_dir()
        assert get_package_root("legacy") is None
        assert list_packages() == []
