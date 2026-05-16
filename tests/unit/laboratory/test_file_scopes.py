"""Unit tests for :mod:`kohakuterrarium.laboratory.adapters.file_scopes`."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.laboratory.adapters.file_scopes import (
    SCOPE_NAMES,
    ScopeError,
    _creature_pwd,
    _ensure_in_root,
    parse_scope,
    resolve_in_scope,
    resolve_scope_root,
)

# ── parse_scope ─────────────────────────────────────────────────


class TestParseScope:
    def test_basic(self):
        assert parse_scope("workspace://cid") == ("workspace", "cid")

    def test_trailing_slash_stripped(self):
        assert parse_scope("workspace://cid/") == ("workspace", "cid")

    def test_empty_arg(self):
        assert parse_scope("config://") == ("config", "")

    def test_missing_separator(self):
        with pytest.raises(ScopeError, match="missing '://'"):
            parse_scope("noseparator")

    def test_empty_name(self):
        with pytest.raises(ScopeError, match="empty scope name"):
            parse_scope("://cid")

    def test_unknown_scope(self):
        with pytest.raises(ScopeError, match="unknown scope"):
            parse_scope("bogus://x")


class TestScopeNames:
    def test_present(self):
        assert "workspace" in SCOPE_NAMES
        assert "memory" in SCOPE_NAMES
        assert "package" in SCOPE_NAMES
        assert "recipe" in SCOPE_NAMES
        assert "config" in SCOPE_NAMES


# ── _ensure_in_root ─────────────────────────────────────────────


class TestEnsureInRoot:
    def test_empty_returns_root(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        assert _ensure_in_root(root, "") == root.resolve()

    def test_rejects_absolute(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        with pytest.raises(ScopeError, match="absolute"):
            _ensure_in_root(root, str(tmp_path / "outside.txt"))

    def test_rejects_parent_dir_traversal(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        with pytest.raises(ScopeError, match="not allowed"):
            _ensure_in_root(root, "../escape.txt")

    def test_normal_path(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        out = _ensure_in_root(root, "sub/file.txt")
        assert out == (root / "sub" / "file.txt").resolve()


# ── _creature_pwd ───────────────────────────────────────────────


class TestCreaturePwd:
    def test_from_executor_working_dir(self, tmp_path):
        agent = SimpleNamespace(
            executor=SimpleNamespace(_working_dir=tmp_path),
            config=None,
        )
        creature = SimpleNamespace(agent=agent)
        assert _creature_pwd(creature) == str(tmp_path)

    def test_empty_working_dir_falls_back(self, tmp_path):
        agent = SimpleNamespace(
            executor=SimpleNamespace(_working_dir=""),
            config=SimpleNamespace(pwd=str(tmp_path)),
        )
        creature = SimpleNamespace(agent=agent)
        assert _creature_pwd(creature) == str(tmp_path)

    def test_fallback_to_agent_path(self, tmp_path):
        agent = SimpleNamespace(
            executor=None,
            config=SimpleNamespace(pwd=None, agent_path=str(tmp_path)),
        )
        creature = SimpleNamespace(agent=agent)
        assert _creature_pwd(creature) == str(tmp_path)

    def test_returns_none_when_no_info(self):
        agent = SimpleNamespace(executor=None, config=None)
        creature = SimpleNamespace(agent=agent)
        assert _creature_pwd(creature) is None


# ── resolve_scope_root ──────────────────────────────────────────


class _FakeEngine:
    def __init__(self, creatures=None):
        self._creatures = creatures or {}

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]


class TestResolveScopeRoot:
    def test_config_scope(self, tmp_path, monkeypatch):
        # Verify the HOME-derived fallback, not the env override the
        # conftest autouse fixture sets — drop ``KT_CONFIG_DIR`` first.
        monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        # On Windows ``Path.home()`` uses USERPROFILE, so also set that.
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        out = resolve_scope_root("config://", _FakeEngine())
        # The base is ``~/.kohakuterrarium``; the dir is created.
        assert out.name == ".kohakuterrarium"
        assert out.is_dir()

    def test_config_with_arg_rejected(self):
        with pytest.raises(ScopeError, match="takes no argument"):
            resolve_scope_root("config://something", _FakeEngine())

    def test_recipe_creates_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        out = resolve_scope_root("recipe://r1", _FakeEngine())
        assert out.name == "r1"
        assert out.is_dir()

    def test_recipe_requires_arg(self):
        with pytest.raises(ScopeError, match="requires an id"):
            resolve_scope_root("recipe://", _FakeEngine())

    def test_package_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(ScopeError, match="not installed"):
            resolve_scope_root("package://missing-pkg", _FakeEngine())

    def test_package_requires_arg(self):
        with pytest.raises(ScopeError, match="requires a name"):
            resolve_scope_root("package://", _FakeEngine())

    def test_workspace_requires_creature_id(self):
        with pytest.raises(ScopeError, match="requires a creature_id"):
            resolve_scope_root("workspace://", _FakeEngine())

    def test_workspace_uses_creature_pwd(self, tmp_path):
        agent = SimpleNamespace(
            executor=SimpleNamespace(_working_dir=tmp_path),
            config=None,
        )
        creature = SimpleNamespace(agent=agent)
        engine = _FakeEngine({"cid": creature})
        out = resolve_scope_root("workspace://cid", engine)
        assert Path(out) == Path(str(tmp_path))

    def test_workspace_no_pwd_raises(self):
        agent = SimpleNamespace(executor=None, config=None)
        creature = SimpleNamespace(agent=agent)
        engine = _FakeEngine({"cid": creature})
        with pytest.raises(ScopeError, match="no working directory"):
            resolve_scope_root("workspace://cid", engine)

    def test_memory_uses_creature_subdir(self, tmp_path):
        agent = SimpleNamespace(
            executor=SimpleNamespace(_working_dir=tmp_path),
            config=None,
        )
        creature = SimpleNamespace(agent=agent)
        engine = _FakeEngine({"cid": creature})
        out = resolve_scope_root("memory://cid", engine)
        assert out.name == "memory"

    def test_memory_requires_creature_id(self):
        with pytest.raises(ScopeError, match="requires a creature_id"):
            resolve_scope_root("memory://", _FakeEngine())

    def test_memory_no_pwd_raises(self):
        # A creature with no resolvable working dir can't host a memory
        # scope — resolve must refuse rather than return a bogus path.
        agent = SimpleNamespace(executor=None, config=None)
        creature = SimpleNamespace(agent=agent)
        engine = _FakeEngine({"cid": creature})
        with pytest.raises(ScopeError, match="no working directory"):
            resolve_scope_root("memory://cid", engine)

    def test_package_installed_returns_package_dir(self, tmp_path, monkeypatch):
        # When the named package IS installed under the packages dir,
        # resolve returns that directory.  Verify the HOME-derived
        # fallback rather than the autouse ``KT_CONFIG_DIR`` env.
        monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        pkg_dir = tmp_path / ".kohakuterrarium" / "packages" / "mypkg"
        pkg_dir.mkdir(parents=True)
        out = resolve_scope_root("package://mypkg", _FakeEngine())
        assert Path(out) == pkg_dir


# ── resolve_in_scope ────────────────────────────────────────────


class TestResolveInScope:
    def test_basic(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        out = resolve_in_scope("recipe://r1", "file.txt", _FakeEngine())
        assert out.name == "file.txt"

    def test_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        with pytest.raises(ScopeError, match="not allowed"):
            resolve_in_scope("recipe://r1", "../escape.txt", _FakeEngine())
