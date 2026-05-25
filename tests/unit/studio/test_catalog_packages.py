"""Unit tests for :mod:`kohakuterrarium.studio.catalog.packages` and
:mod:`kohakuterrarium.studio.catalog.spawnable`.
"""

from pathlib import Path


from kohakuterrarium.studio.catalog import packages as pkg_mod
from kohakuterrarium.studio.catalog import spawnable as spawn_mod

# ── normalize_package_name ───────────────────────────────────


class TestNormalizePackageName:
    def test_empty(self):
        assert pkg_mod.normalize_package_name("") == ""
        assert pkg_mod.normalize_package_name("   ") == ""

    def test_at_prefix_stripped(self):
        assert pkg_mod.normalize_package_name("@demo") == "demo"

    def test_path_stripped(self):
        assert pkg_mod.normalize_package_name("@demo/creatures/x") == "demo"

    def test_no_at_prefix(self):
        assert pkg_mod.normalize_package_name("demo") == "demo"


# ── packages_dir / list_installed_packages ──────────────────


class TestPassthroughs:
    def test_packages_dir_returns_configured_constant(self):
        from kohakuterrarium.packages.locations import PACKAGES_DIR

        # The helper is pure indirection over the locations constant.
        assert pkg_mod.packages_dir() == PACKAGES_DIR

    def test_list_installed_packages_delegates(self, monkeypatch):
        sentinel = [{"name": "demo"}]
        monkeypatch.setattr(pkg_mod, "list_packages", lambda: sentinel)
        # Returns the underlying list_packages result unchanged.
        assert pkg_mod.list_installed_packages() == sentinel


# ── install_package_op / uninstall_package_op ───────────────


class TestInstallUninstall:
    def test_install_passes_through(self, monkeypatch):
        captured = []

        def _install(src, *, editable, name_override):
            captured.append((src, editable, name_override))
            return "demo"

        # ``install_package_op`` now routes through ``install_package_spec``
        # (which handles ``@`` marketplace specs).  Non-spec inputs fall
        # straight through to ``install_package``, but the test monkeypatch
        # has to land on the spec wrapper since that's the now-immediate
        # callee.
        monkeypatch.setattr(pkg_mod, "install_package_spec", _install)
        out = pkg_mod.install_package_op("git+https://x", editable=True, name="d")
        assert out == "demo"
        assert captured == [("git+https://x", True, "d")]

    def test_uninstall_passes_through(self, monkeypatch):
        monkeypatch.setattr(pkg_mod, "uninstall_package", lambda n: True)
        assert pkg_mod.uninstall_package_op("demo") is True

    # ── scan-cache invalidation contract ────────────────────────
    # The 10s TTL on packages_scan._creatures_cache /
    # _terrariums_cache used to outlive a fresh install /
    # uninstall / update, so /api/configs/{creatures,terrariums}
    # would echo the pre-install state for up to 10 seconds.
    # The frontend NewCreatureModal then showed "No creature
    # configs available" even when the user had just installed a
    # package that contained creature configs.  Pin the invalidation
    # contract here so the bug can't quietly come back.

    def test_install_invalidates_scan_cache(self, monkeypatch):
        from kohakuterrarium.studio.catalog import packages_scan as scan_mod

        called = []
        monkeypatch.setattr(pkg_mod, "install_package_spec", lambda *a, **kw: "newpkg")
        monkeypatch.setattr(
            scan_mod, "invalidate_scan_caches", lambda: called.append("invalidated")
        )
        # The module imports the symbol directly, so monkeypatch the
        # importing module's local binding too.
        monkeypatch.setattr(
            pkg_mod, "invalidate_scan_caches", lambda: called.append("invalidated")
        )

        pkg_mod.install_package_op("git+https://x")
        assert called == ["invalidated"]

    def test_uninstall_invalidates_scan_cache_on_removal(self, monkeypatch):
        called = []
        monkeypatch.setattr(pkg_mod, "uninstall_package", lambda n: True)
        monkeypatch.setattr(
            pkg_mod, "invalidate_scan_caches", lambda: called.append("invalidated")
        )

        assert pkg_mod.uninstall_package_op("demo") is True
        assert called == ["invalidated"]

    def test_uninstall_skips_invalidation_when_nothing_removed(self, monkeypatch):
        # Don't bust the cache when the package wasn't actually
        # removed — the on-disk state didn't change, so a needless
        # cache miss penalises the next catalog read for no benefit.
        called = []
        monkeypatch.setattr(pkg_mod, "uninstall_package", lambda n: False)
        monkeypatch.setattr(
            pkg_mod, "invalidate_scan_caches", lambda: called.append("invalidated")
        )

        assert pkg_mod.uninstall_package_op("ghost") is False
        assert called == []

    def test_update_invalidates_scan_cache_on_success(self, monkeypatch, tmp_path):
        # update_package_op success path must invalidate — the new
        # revision may have added / removed creature manifests, and
        # update_all_packages_op() composes update_package_op() per
        # package so this also covers the bulk path.
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(
            pkg_mod,
            "list_packages",
            lambda: [{"name": "demo", "path": str(tmp_path), "editable": False}],
        )
        monkeypatch.setattr(pkg_mod, "update_package", lambda n: None)
        called = []
        monkeypatch.setattr(
            pkg_mod, "invalidate_scan_caches", lambda: called.append("invalidated")
        )

        rc, _ = pkg_mod.update_package_op("demo")
        assert rc == 0
        assert called == ["invalidated"]


# ── update_package_op ────────────────────────────────────────


class TestUpdatePackageOp:
    def test_package_not_found(self, monkeypatch):
        monkeypatch.setattr(pkg_mod, "list_packages", lambda: [])
        rc, msg = pkg_mod.update_package_op("ghost")
        assert rc == 1
        assert "not found" in msg

    def test_editable_skipped(self, monkeypatch):
        monkeypatch.setattr(
            pkg_mod,
            "list_packages",
            lambda: [{"name": "demo", "path": "/p", "editable": True}],
        )
        rc, msg = pkg_mod.update_package_op("demo")
        assert rc == 0
        assert "editable" in msg

    def test_non_git_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            pkg_mod,
            "list_packages",
            lambda: [{"name": "demo", "path": str(tmp_path), "editable": False}],
        )
        rc, msg = pkg_mod.update_package_op("demo")
        assert rc == 0
        assert "non-git" in msg

    def test_update_failure(self, monkeypatch, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.setattr(
            pkg_mod,
            "list_packages",
            lambda: [{"name": "demo", "path": str(tmp_path), "editable": False}],
        )

        def _boom(name):
            raise RuntimeError("git failed")

        monkeypatch.setattr(pkg_mod, "update_package", _boom)
        rc, msg = pkg_mod.update_package_op("demo")
        assert rc == 1
        assert "Failed to update" in msg

    def test_update_success(self, monkeypatch, tmp_path):
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(
            pkg_mod,
            "list_packages",
            lambda: [{"name": "demo", "path": str(tmp_path), "editable": False}],
        )
        monkeypatch.setattr(pkg_mod, "update_package", lambda n: None)
        rc, msg = pkg_mod.update_package_op("demo")
        assert rc == 0
        assert "Updated" in msg


# ── update_all_packages_op ──────────────────────────────────


class TestUpdateAllPackagesOp:
    def test_no_packages(self, monkeypatch):
        monkeypatch.setattr(pkg_mod, "list_packages", lambda: [])
        rc, msgs, upd, skip = pkg_mod.update_all_packages_op()
        assert rc == 0
        assert "No packages installed" in msgs[0]
        assert upd == 0
        assert skip == 0

    def test_mixed_editable_and_git(self, monkeypatch, tmp_path):
        git_p = tmp_path / "pg"
        git_p.mkdir()
        (git_p / ".git").mkdir()
        editable_p = tmp_path / "pe"
        editable_p.mkdir()
        nogit_p = tmp_path / "pn"
        nogit_p.mkdir()
        packages = [
            {"name": "edit", "path": str(editable_p), "editable": True},
            {"name": "nogit", "path": str(nogit_p), "editable": False},
            {"name": "git", "path": str(git_p), "editable": False},
        ]
        monkeypatch.setattr(pkg_mod, "list_packages", lambda: packages)
        monkeypatch.setattr(pkg_mod, "update_package", lambda n: None)
        rc, msgs, upd, skip = pkg_mod.update_all_packages_op()
        assert upd == 1
        assert skip == 2
        assert rc == 0

    def test_update_failure_sets_exit_code(self, monkeypatch, tmp_path):
        git_p = tmp_path / "pg"
        git_p.mkdir()
        (git_p / ".git").mkdir()
        packages = [{"name": "git", "path": str(git_p), "editable": False}]
        monkeypatch.setattr(pkg_mod, "list_packages", lambda: packages)

        def _boom(n):
            raise RuntimeError("fail")

        monkeypatch.setattr(pkg_mod, "update_package", _boom)
        rc, msgs, upd, skip = pkg_mod.update_all_packages_op()
        assert rc == 1


# ── load_agent_info ──────────────────────────────────────────


class TestLoadAgentInfo:
    def test_path_not_found(self, tmp_path):
        missing = str(tmp_path / "ghost")
        rc, payload = pkg_mod.load_agent_info(missing)
        assert rc == 1
        assert payload == f"Agent path not found: {missing}"

    def test_no_config_yaml(self, tmp_path):
        d = tmp_path / "p"
        d.mkdir()
        rc, payload = pkg_mod.load_agent_info(str(d))
        assert rc == 1
        assert payload == f"No config.yaml found in {d}"

    def test_yaml_with_config_yml_fallback(self, tmp_path):
        d = tmp_path / "p"
        d.mkdir()
        (d / "config.yml").write_text(
            "name: alice\nmodel: m\ntools:\n  - bash\nsubagents:\n  - {name: explorer}\n"
        )
        (d / "other.txt").write_text("x")
        rc, payload = pkg_mod.load_agent_info(str(d))
        assert rc == 0
        assert payload["name"] == "alice"
        assert payload["tools"] == ["bash"]
        assert payload["subagents"] == ["explorer"]

    def test_yaml_parse_error(self, tmp_path):
        d = tmp_path / "p"
        d.mkdir()
        (d / "config.yaml").write_text(":\n: not yaml")
        rc, payload = pkg_mod.load_agent_info(str(d))
        assert rc == 1
        assert "Error reading config" in payload

    def test_dict_tools_and_subagents(self, tmp_path):
        d = tmp_path / "p"
        d.mkdir()
        (d / "config.yaml").write_text(
            "name: alice\ntools:\n  - {name: bash}\n  - shell\n"
            "subagents:\n  - {name: explorer}\n  - simple\n"
        )
        rc, payload = pkg_mod.load_agent_info(str(d))
        assert rc == 0
        assert "bash" in payload["tools"]
        assert "shell" in payload["tools"]
        assert "explorer" in payload["subagents"]
        assert "simple" in payload["subagents"]


# ── resolve_edit_target ─────────────────────────────────────


class TestResolveEditTarget:
    def test_unresolvable_returns_error(self, monkeypatch):
        def boom(target):
            raise FileNotFoundError("no such pkg")

        monkeypatch.setattr(pkg_mod, "resolve_package_path", boom)
        rc, payload = pkg_mod.resolve_edit_target("@ghost")
        assert rc == 1
        assert "no such" in payload

    def test_value_error_returned(self, monkeypatch):
        def boom(target):
            raise ValueError("bad ref")

        monkeypatch.setattr(pkg_mod, "resolve_package_path", boom)
        rc, payload = pkg_mod.resolve_edit_target("@x")
        assert rc == 1

    def test_finds_config_file(self, monkeypatch, tmp_path):
        target_dir = tmp_path / "creature"
        target_dir.mkdir()
        (target_dir / "config.yaml").write_text("name: x")
        monkeypatch.setattr(pkg_mod, "resolve_package_path", lambda t: target_dir)
        rc, payload = pkg_mod.resolve_edit_target("@pkg/creature")
        assert rc == 0
        assert isinstance(payload, Path)
        assert payload.name == "config.yaml"

    def test_no_at_prefix_added(self, monkeypatch, tmp_path):
        d = tmp_path / "x"
        d.mkdir()
        (d / "terrarium.yaml").write_text("name: t")
        monkeypatch.setattr(
            pkg_mod, "resolve_package_path", lambda t: d if t.startswith("@") else None
        )
        rc, payload = pkg_mod.resolve_edit_target("pkg/x")
        assert rc == 0
        assert payload.name == "terrarium.yaml"

    def test_direct_file_target(self, monkeypatch, tmp_path):
        f = tmp_path / "f.yaml"
        f.write_text("name: x")
        monkeypatch.setattr(pkg_mod, "resolve_package_path", lambda t: f)
        rc, payload = pkg_mod.resolve_edit_target("@f")
        assert rc == 0
        assert payload == f

    def test_no_config_file_in_dir(self, monkeypatch, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        monkeypatch.setattr(pkg_mod, "resolve_package_path", lambda t: d)
        rc, payload = pkg_mod.resolve_edit_target("@empty")
        assert rc == 1
        assert "No config file" in payload


# ── spawnable ────────────────────────────────────────────────


class TestListSpawnableCreatures:
    def test_no_workspace_and_no_packages(self, monkeypatch):
        monkeypatch.setattr(spawn_mod, "list_packages", lambda: [])
        out = spawn_mod.list_spawnable_creatures(workspace=None)
        assert out == []

    def test_workspace_creatures(self, monkeypatch):
        monkeypatch.setattr(spawn_mod, "list_packages", lambda: [])

        class _WS:
            def list_creatures(self):
                return [
                    {
                        "name": "alice",
                        "path": "/p/alice",
                        "description": "first",
                    }
                ]

        out = spawn_mod.list_spawnable_creatures(workspace=_WS())
        assert out[0]["name"] == "alice"
        assert out[0]["source"] == "workspace"

    def test_workspace_raises_swallowed(self, monkeypatch):
        monkeypatch.setattr(spawn_mod, "list_packages", lambda: [])

        class _BadWS:
            def list_creatures(self):
                raise RuntimeError("bad")

        out = spawn_mod.list_spawnable_creatures(workspace=_BadWS())
        assert out == []

    def test_package_creatures(self, monkeypatch):
        monkeypatch.setattr(
            spawn_mod,
            "list_packages",
            lambda: [
                {
                    "name": "demo",
                    "creatures": [
                        {"name": "bob", "description": "second"},
                        "not-a-dict",
                        {"name": ""},  # empty name skipped
                    ],
                }
            ],
        )
        out = spawn_mod.list_spawnable_creatures(workspace=None)
        # Only valid bob entry.
        assert len(out) == 1
        assert out[0]["name"] == "bob"
        assert out[0]["source"] == "package"
        assert out[0]["ref"] == "@demo/creatures/bob"

    def test_package_without_name_skipped(self, monkeypatch):
        monkeypatch.setattr(
            spawn_mod,
            "list_packages",
            lambda: [{"creatures": [{"name": "x"}]}],  # no pkg name
        )
        out = spawn_mod.list_spawnable_creatures()
        assert out == []
