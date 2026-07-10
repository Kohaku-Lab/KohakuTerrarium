"""Unit tests for :func:`kohakuterrarium.cli.resume._resolve_missing_pwd`."""

import os
import types

from kohakuterrarium.cli import resume as resume_mod
from kohakuterrarium.cli.resume import _resolve_missing_pwd
from kohakuterrarium.session.store import SessionStore


def _make_store(tmp_path, pwd: str):
    path = tmp_path / "s.kohakutr"
    store = SessionStore(str(path))
    store.init_meta(
        session_id="s1",
        config_type="agent",
        config_path="x",
        pwd=pwd,
        agents=["a"],
    )
    store.close()
    return path


def _fake_stdin(monkeypatch, *, tty: bool) -> None:
    monkeypatch.setattr(
        resume_mod.sys, "stdin", types.SimpleNamespace(isatty=lambda: tty)
    )


class TestResolveMissingPwd:
    def test_explicit_override_wins(self, tmp_path):
        path = _make_store(tmp_path, str(tmp_path / "gone"))
        assert _resolve_missing_pwd(path, "/explicit") == "/explicit"

    def test_existing_saved_dir_untouched(self, tmp_path):
        path = _make_store(tmp_path, str(tmp_path))
        assert _resolve_missing_pwd(path, None) is None

    def test_missing_dir_non_tty_falls_back(self, tmp_path, monkeypatch, capsys):
        path = _make_store(tmp_path, str(tmp_path / "gone"))
        _fake_stdin(monkeypatch, tty=False)
        assert _resolve_missing_pwd(path, None) is None
        assert "saved working dir missing" in capsys.readouterr().out.lower()

    def test_missing_dir_prompts_until_valid(self, tmp_path, monkeypatch):
        path = _make_store(tmp_path, str(tmp_path / "gone"))
        _fake_stdin(monkeypatch, tty=True)
        answers = iter(["not_a_real_dir", str(tmp_path)])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
        assert _resolve_missing_pwd(path, None) == str(tmp_path)

    def test_missing_dir_empty_answer_uses_cwd(self, tmp_path, monkeypatch):
        path = _make_store(tmp_path, str(tmp_path / "gone"))
        _fake_stdin(monkeypatch, tty=True)
        monkeypatch.setattr("builtins.input", lambda _prompt: "")
        assert _resolve_missing_pwd(path, None) == os.getcwd()
