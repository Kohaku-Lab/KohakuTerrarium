"""Coverage tests for the remaining studio mid-tier modules."""

import base64
import json
from types import SimpleNamespace

import pytest

from kohakuterrarium.studio import files as files_mod
from kohakuterrarium.studio.editors import (
    skills_state as ss_mod,
    utils_paths as up_mod,
)
from kohakuterrarium.studio.sessions import (
    creature_command as cc_mod,
    creature_model as cm_mod,
)

# ── editors/utils_paths ─────────────────────────────────────


class TestSanitizeName:
    def test_valid(self):
        assert up_mod.sanitize_name("alice") == "alice"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            up_mod.sanitize_name("")

    def test_whitespace_raises(self):
        with pytest.raises(ValueError):
            up_mod.sanitize_name("  alice  ")

    def test_dot_prefix_raises(self):
        with pytest.raises(ValueError):
            up_mod.sanitize_name(".hidden")

    def test_path_separator_raises(self):
        with pytest.raises(ValueError):
            up_mod.sanitize_name("a/b")

    def test_backslash_separator_raises(self):
        with pytest.raises(ValueError):
            up_mod.sanitize_name("a\\b")

    def test_parent_ref_raises(self):
        with pytest.raises(ValueError):
            up_mod.sanitize_name("..")

    def test_windows_reserved_raises(self):
        with pytest.raises(ValueError):
            up_mod.sanitize_name("con")
        with pytest.raises(ValueError):
            up_mod.sanitize_name("COM1")

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            up_mod.sanitize_name(123)


class TestEnsureInRoot:
    def test_empty_raises(self, tmp_path):
        with pytest.raises(up_mod.UnsafePath):
            up_mod.ensure_in_root(tmp_path, "")

    def test_absolute_raises(self, tmp_path):
        with pytest.raises(up_mod.UnsafePath):
            up_mod.ensure_in_root(tmp_path, "/etc/passwd")

    def test_escape_raises(self, tmp_path):
        with pytest.raises(up_mod.UnsafePath):
            up_mod.ensure_in_root(tmp_path, "../escape")

    def test_valid(self, tmp_path):
        out = up_mod.ensure_in_root(tmp_path, "sub/file.txt")
        assert out.is_relative_to(tmp_path)


# ── editors/skills_state ────────────────────────────────────


class TestSkillsState:
    def test_load_state_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "ghost_dir"))
        assert ss_mod.load_state() == {}

    def test_load_state_invalid_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        (tmp_path / "skill_state.json").write_text("not json")
        assert ss_mod.load_state() == {}

    def test_load_state_not_dict(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        (tmp_path / "skill_state.json").write_text('["list", "not", "dict"]')
        assert ss_mod.load_state() == {}

    def test_load_state_valid(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        (tmp_path / "skill_state.json").write_text(
            '{"skill_a": true, "skill_b": false}'
        )
        out = ss_mod.load_state()
        assert out == {"skill_a": True, "skill_b": False}

    def test_save_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        ss_mod.save_state({"x": True})
        assert json.loads((tmp_path / "skill_state.json").read_text()) == {"x": True}

    def test_serialize_uses_state_override(self, monkeypatch):
        skill = SimpleNamespace(
            name="myskill",
            description="d",
            origin="user",
            enabled=False,
            disable_model_invocation=False,
            paths=[],
            allowed_tools=[],
            base_dir=None,
        )
        out = ss_mod.serialize(skill, {"myskill": True})
        # State override wins.
        assert out["enabled"] is True

    def test_serialize_uses_default_when_no_override(self):
        skill = SimpleNamespace(
            name="myskill",
            description="d",
            origin="user",
            enabled=True,
            disable_model_invocation=False,
            paths=[],
            allowed_tools=[],
            base_dir=None,
        )
        out = ss_mod.serialize(skill, {})
        assert out["enabled"] is True


# ── studio.files (RemoteFiles) ──────────────────────────────


class _FakeSender:
    def __init__(self, responses=None):
        self._responses = responses or {}
        self.calls = []

    async def request(self, *, to_node, namespace, type, body, timeout):
        self.calls.append((type, body))
        return self._responses.get(type, {})


class TestRemoteFiles:
    def test_init_basic(self):
        sender = _FakeSender()
        rf = files_mod.RemoteFiles(sender, "worker-1")
        assert rf.target_node == "worker-1"

    async def test_list(self):
        sender = _FakeSender(responses={"list": {"entries": [{"name": "a"}]}})
        rf = files_mod.RemoteFiles(sender, "w1")
        out = await rf.list("config://", "")
        assert out == [{"name": "a"}]

    async def test_stat(self):
        sender = _FakeSender(responses={"stat": {"stat": {"size": 10}}})
        rf = files_mod.RemoteFiles(sender, "w1")
        out = await rf.stat("config://", "x")
        assert out == {"size": 10}

    async def test_read(self):
        data = b"hello"
        sender = _FakeSender(
            responses={
                "read": {
                    "bytes_b64": base64.b64encode(data).decode("ascii"),
                    "sha256": "deadbeef",
                }
            }
        )
        rf = files_mod.RemoteFiles(sender, "w1")
        body, h = await rf.read("config://", "x")
        assert body == data
        assert h == "deadbeef"

    async def test_write(self):
        sender = _FakeSender(responses={"write": {"written": 5, "sha256": "abc"}})
        rf = files_mod.RemoteFiles(sender, "w1")
        written, h = await rf.write("config://", "x", b"hello", expect_hash="prev")
        assert written == 5

    async def test_delete(self):
        sender = _FakeSender(responses={"delete": {}})
        rf = files_mod.RemoteFiles(sender, "w1")
        await rf.delete("config://", "x")

    async def test_push_bundle(self):
        sender = _FakeSender(
            responses={
                "push_bundle": {
                    "deployed": ["a", "b"],
                    "conflicts": [],
                }
            }
        )
        rf = files_mod.RemoteFiles(sender, "w1")
        out = await rf.push_bundle(
            "recipe://", {"a": ("h1", b"data1"), "b": ("h2", b"data2")}
        )
        assert out["deployed"] == ["a", "b"]

    async def test_push_bundle_with_partial(self):
        sender = _FakeSender(
            responses={
                "push_bundle": {
                    "deployed": ["a"],
                    "conflicts": [],
                    "partial": True,
                    "remaining": ["b"],
                }
            }
        )
        rf = files_mod.RemoteFiles(sender, "w1")
        out = await rf.push_bundle("recipe://", {"a": ("h", b"x")})
        assert out["partial"] is True
        assert out["remaining"] == ["b"]


class TestMaybeRaise:
    def test_passes_through_non_error(self):
        out = files_mod._maybe_raise({"ok": True})
        assert out == {"ok": True}

    def test_not_found(self):
        with pytest.raises(FileNotFoundError):
            files_mod._maybe_raise(
                {"error": {"kind": "not_found", "message": "missing"}}
            )

    def test_invalid(self):
        with pytest.raises(ValueError):
            files_mod._maybe_raise({"error": {"kind": "invalid", "message": "bad"}})

    def test_denied(self):
        with pytest.raises(PermissionError):
            files_mod._maybe_raise({"error": {"kind": "denied", "message": "no"}})

    def test_other_kind(self):
        with pytest.raises(files_mod.RemoteFilesError):
            files_mod._maybe_raise({"error": {"kind": "unknown", "message": "weird"}})

    def test_non_dict_passes(self):
        assert files_mod._maybe_raise("not-a-dict") == "not-a-dict"


# ── studio.sessions.creature_command ────────────────────────


class TestCreatureCommand:
    async def test_unknown_command_raises(self, monkeypatch):
        agent = SimpleNamespace()
        creature = SimpleNamespace(agent=agent)
        engine = SimpleNamespace()
        monkeypatch.setattr(cc_mod, "find_creature", lambda e, s, c: creature)
        monkeypatch.setattr(cc_mod, "as_engine", lambda s: engine)
        monkeypatch.setattr(cc_mod, "get_builtin_user_command", lambda n: None)
        with pytest.raises(ValueError, match="Unknown command"):
            await cc_mod.execute_command(SimpleNamespace(), "_", "alice", "ghost")

    async def test_known_command_executes(self, monkeypatch):
        from types import SimpleNamespace as SN

        agent = SN(session=None)
        creature = SN(agent=agent)
        engine = SN()

        # Fake command with async execute.
        result = SN(
            output="hello",
            error="",
            success=True,
            data={"k": "v"},
        )

        class _Cmd:
            async def execute(self, args, ctx):
                return result

        monkeypatch.setattr(cc_mod, "find_creature", lambda e, s, c: creature)
        monkeypatch.setattr(cc_mod, "as_engine", lambda s: engine)
        monkeypatch.setattr(cc_mod, "get_builtin_user_command", lambda n: _Cmd())
        out = await cc_mod.execute_command(SN(), "_", "alice", "info", "arg")
        assert out["command"] == "info"
        assert out["data"] == {"k": "v"}

    async def test_no_data_omitted(self, monkeypatch):
        from types import SimpleNamespace as SN

        agent = SN(session=None)
        creature = SN(agent=agent)
        engine = SN()
        result = SN(output="x", error="", success=True, data=None)

        class _Cmd:
            async def execute(self, args, ctx):
                return result

        monkeypatch.setattr(cc_mod, "find_creature", lambda e, s, c: creature)
        monkeypatch.setattr(cc_mod, "as_engine", lambda s: engine)
        monkeypatch.setattr(cc_mod, "get_builtin_user_command", lambda n: _Cmd())
        out = await cc_mod.execute_command(SN(), "_", "alice", "info")
        assert "data" not in out


# ── studio.sessions.creature_model ──────────────────────────


class TestCreatureModel:
    def test_switch_model(self, monkeypatch):
        from types import SimpleNamespace as SN

        agent = SN(switch_model=lambda p: f"switched-{p}")
        creature = SN(agent=agent)
        monkeypatch.setattr(cm_mod, "find_creature", lambda e, s, c: creature)
        monkeypatch.setattr(cm_mod, "as_engine", lambda s: SN())
        out = cm_mod.switch_model(SN(), "_", "alice", "openai/gpt-4")
        assert out == "switched-openai/gpt-4"

    def test_set_native_tool_options_missing_helper(self, monkeypatch):
        from types import SimpleNamespace as SN

        agent = SN(native_tool_options=None)
        creature = SN(agent=agent)
        monkeypatch.setattr(cm_mod, "find_creature", lambda e, s, c: creature)
        monkeypatch.setattr(cm_mod, "as_engine", lambda s: SN())
        with pytest.raises(ValueError, match="native_tool_options"):
            cm_mod.set_native_tool_options(SN(), "_", "alice", "tool", {"k": "v"})

    def test_set_native_tool_options_success(self, monkeypatch):
        from types import SimpleNamespace as SN

        helper = SN(set=lambda t, v: {"updated": True, **v})
        agent = SN(native_tool_options=helper)
        creature = SN(agent=agent)
        monkeypatch.setattr(cm_mod, "find_creature", lambda e, s, c: creature)
        monkeypatch.setattr(cm_mod, "as_engine", lambda s: SN())
        out = cm_mod.set_native_tool_options(SN(), "_", "alice", "tool", {"k": "v"})
        assert out["k"] == "v"

    def test_set_native_tool_options_empty_values(self, monkeypatch):
        from types import SimpleNamespace as SN

        helper = SN(set=lambda t, v: v)
        agent = SN(native_tool_options=helper)
        creature = SN(agent=agent)
        monkeypatch.setattr(cm_mod, "find_creature", lambda e, s, c: creature)
        monkeypatch.setattr(cm_mod, "as_engine", lambda s: SN())
        out = cm_mod.set_native_tool_options(SN(), "_", "alice", "tool", None)
        assert out == {}
