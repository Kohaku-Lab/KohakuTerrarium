"""Unit tests for the git backend abstraction.

Two backends are exercised:

  1. The native-git path — monkeypatching ``shutil.which`` to return
     a fake binary + capturing ``subprocess.run`` args.
  2. The dulwich pure-Python path — monkeypatching ``shutil.which``
     to return ``None`` + stubbing the ``dulwich.porcelain`` calls.

Both paths share the public ``clone_repo`` / ``pull_repo`` contract
so the test class structure mirrors that symmetry.
"""

import subprocess
import sys
import types

import pytest

from kohakuterrarium.packages import git_backend


@pytest.fixture(autouse=True)
def _reset_backend_cache(monkeypatch):
    """Each test gets a fresh native-git probe."""
    git_backend._reset_backend_cache_for_tests()


class TestBackendSelection:
    def test_picks_native_when_git_on_path(self, monkeypatch):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: "/usr/bin/git")
        assert git_backend._has_native_git() is True

    def test_falls_back_to_dulwich_when_no_git(self, monkeypatch):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: None)
        assert git_backend._has_native_git() is False

    def test_native_probe_is_cached(self, monkeypatch):
        calls = []

        def which(_):
            calls.append(1)
            return "/usr/bin/git"

        monkeypatch.setattr(git_backend.shutil, "which", which)
        git_backend._has_native_git()
        git_backend._has_native_git()
        # Probe only fired once — cached for the second call.
        assert len(calls) == 1


class TestNativeClonePath:
    def test_clone_runs_git_clone(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: "/usr/bin/git")
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(git_backend.subprocess, "run", fake_run)
        git_backend.clone_repo("https://example.com/x.git", tmp_path / "x")
        assert captured["cmd"][:2] == ["git", "clone"]
        assert captured["cmd"][2] == "https://example.com/x.git"
        assert captured["cmd"][3] == str(tmp_path / "x")

    def test_clone_failure_raises_runtime_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: "/usr/bin/git")

        def boom(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd, stderr=b"clone denied")

        monkeypatch.setattr(git_backend.subprocess, "run", boom)
        with pytest.raises(RuntimeError, match="Git clone failed"):
            git_backend.clone_repo("https://example.com/x.git", tmp_path / "x")

    def test_clone_with_ref_uses_dash_b(self, monkeypatch, tmp_path):
        # AUDIT FIX #1: ``ref`` parameter must pass through to
        # ``git clone -b <ref>`` so the cloned working tree is
        # actually pinned to that ref — fixes the silent
        # "kt install @x@v1.2.0 clones default HEAD" bug.
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: "/usr/bin/git")
        captured = {}

        def fake_run(cmd, **kw):
            captured.setdefault("cmds", []).append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(git_backend.subprocess, "run", fake_run)
        git_backend.clone_repo(
            "https://example.com/x.git", tmp_path / "x", ref="v1.2.0"
        )
        # Single call with -b <ref> shape on the happy path.
        assert len(captured["cmds"]) == 1
        cmd = captured["cmds"][0]
        assert cmd[:2] == ["git", "clone"]
        assert "-b" in cmd
        assert cmd[cmd.index("-b") + 1] == "v1.2.0"

    def test_clone_with_sha_ref_falls_back_to_checkout(self, monkeypatch, tmp_path):
        # ``-b <SHA>`` is rejected by native git.  The fallback path
        # must clone bare + ``git checkout <SHA>``.
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: "/usr/bin/git")
        captured = {"cmds": []}
        # First call (clone -b SHA) errors; subsequent succeed.
        call_count = {"n": 0}

        def fake_run(cmd, **kw):
            captured["cmds"].append(cmd)
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise subprocess.CalledProcessError(
                    128, cmd, stderr=b"not a tag/branch"
                )
            return subprocess.CompletedProcess(cmd, 0)

        # Pretend the partial target was created so the cleanup branch runs.
        target = tmp_path / "x"
        target.mkdir(parents=True)
        monkeypatch.setattr(git_backend.subprocess, "run", fake_run)
        git_backend.clone_repo(
            "https://example.com/x.git",
            target,
            ref="deadbeefcafebabedeadbeefcafebabedeadbeef",
        )
        # Three commands total: -b SHA (failed), plain clone, checkout SHA.
        assert len(captured["cmds"]) == 3
        assert "checkout" in captured["cmds"][2]
        assert captured["cmds"][2][-1] == "deadbeefcafebabedeadbeefcafebabedeadbeef"

    def test_checkout_failure_tears_down_target(self, monkeypatch, tmp_path):
        # AUDIT FIX #6 (round-2): clone succeeds but checkout fails →
        # target must be removed, otherwise the next install_package
        # call sees ``target.exists()`` and silently falls through
        # the pull-in-place branch (skipping the requested ref).
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: "/usr/bin/git")
        target = tmp_path / "x"
        call_count = {"n": 0}

        def fake_run(cmd, **kw):
            call_count["n"] += 1
            if cmd[1] == "clone" and "-b" in cmd:
                # First attempt: clone -b SHA fails.
                raise subprocess.CalledProcessError(128, cmd, stderr=b"bad ref")
            if cmd[1] == "clone":
                # Second attempt: plain clone succeeds + creates dir.
                target.mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0)
            if "checkout" in cmd:
                # Third attempt: checkout fails → cleanup should fire.
                raise subprocess.CalledProcessError(1, cmd, stderr=b"unknown ref")
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(git_backend.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="checkout"):
            git_backend.clone_repo("https://example.com/x.git", target, ref="bogus-ref")
        # Cleanup contract: target dir gone so the next attempt
        # starts from scratch.
        assert not target.exists()


class TestNativePullPath:
    def test_pull_runs_git_pull_ff_only(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: "/usr/bin/git")
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(git_backend.subprocess, "run", fake_run)
        git_backend.pull_repo(tmp_path)
        assert captured["cmd"][:2] == ["git", "-C"]
        assert "--ff-only" in captured["cmd"]

    def test_pull_failure_raises_runtime_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: "/usr/bin/git")

        def boom(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd, stderr=b"pull rejected")

        monkeypatch.setattr(git_backend.subprocess, "run", boom)
        with pytest.raises(RuntimeError, match="Git pull failed"):
            git_backend.pull_repo(tmp_path)


class TestDulwichClonePath:
    def test_clone_invokes_porcelain_clone(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: None)
        captured = {}

        fake_porcelain = types.SimpleNamespace(
            clone=lambda url, target, depth=None: captured.update(
                {"url": url, "target": target, "depth": depth}
            )
        )
        fake_dulwich = types.ModuleType("dulwich")
        fake_dulwich.porcelain = fake_porcelain
        monkeypatch.setitem(sys.modules, "dulwich", fake_dulwich)
        monkeypatch.setitem(sys.modules, "dulwich.porcelain", fake_porcelain)
        git_backend.clone_repo("https://example.com/x.git", tmp_path / "x")
        assert captured["url"] == "https://example.com/x.git"
        assert captured["target"] == str(tmp_path / "x")
        # We pass depth=1 to keep clone size small on resource-bound
        # devices (Android, low-RAM hosts).
        assert captured["depth"] == 1

    def test_clone_failure_raises_runtime_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: None)

        def boom(*args, **kw):
            raise OSError("network down")

        fake_porcelain = types.SimpleNamespace(clone=boom)
        fake_dulwich = types.ModuleType("dulwich")
        fake_dulwich.porcelain = fake_porcelain
        monkeypatch.setitem(sys.modules, "dulwich", fake_dulwich)
        monkeypatch.setitem(sys.modules, "dulwich.porcelain", fake_porcelain)
        with pytest.raises(RuntimeError, match="Git clone failed"):
            git_backend.clone_repo("https://example.com/x.git", tmp_path / "x")


class TestDulwichPullPath:
    def test_pull_invokes_porcelain_pull(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: None)
        (tmp_path / ".git").mkdir()
        captured = {}

        fake_porcelain = types.SimpleNamespace(
            pull=lambda target: captured.update({"target": target})
        )
        fake_dulwich = types.ModuleType("dulwich")
        fake_dulwich.porcelain = fake_porcelain
        monkeypatch.setitem(sys.modules, "dulwich", fake_dulwich)
        monkeypatch.setitem(sys.modules, "dulwich.porcelain", fake_porcelain)
        git_backend.pull_repo(tmp_path)
        assert captured["target"] == str(tmp_path)

    def test_pull_without_git_dir_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: None)
        # Stub a working dulwich import so the .git-dir check is what
        # raises (not the dulwich-missing path tested below).
        fake_porcelain = types.SimpleNamespace(pull=lambda *_a, **_kw: None)
        fake_dulwich = types.ModuleType("dulwich")
        fake_dulwich.porcelain = fake_porcelain
        monkeypatch.setitem(sys.modules, "dulwich", fake_dulwich)
        monkeypatch.setitem(sys.modules, "dulwich.porcelain", fake_porcelain)
        # No .git dir → dulwich path must refuse before invoking
        # porcelain.pull (which would otherwise raise a less-clear
        # IOError from down in libgit-style code).
        with pytest.raises(RuntimeError, match="Not a git clone"):
            git_backend.pull_repo(tmp_path)

    def test_no_backend_available_raises(self, monkeypatch, tmp_path):
        # Force the native probe to miss AND make dulwich import fail.
        monkeypatch.setattr(git_backend.shutil, "which", lambda _: None)
        # Remove any cached dulwich import so the lazy probe re-tries.
        monkeypatch.setitem(sys.modules, "dulwich", None)
        with pytest.raises(RuntimeError, match="No git available"):
            git_backend.clone_repo("https://example.com/x.git", tmp_path / "x")
