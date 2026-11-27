"""Unit tests for :func:`kohakuterrarium.cli.resume._resolve_missing_pwd`."""

import types

import pytest

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

    def test_missing_dir_non_tty_cancels(self, tmp_path, monkeypatch, capsys):
        path = _make_store(tmp_path, str(tmp_path / "gone"))
        _fake_stdin(monkeypatch, tty=False)
        assert _resolve_missing_pwd(path, None) is False
        assert (
            "cannot resume without an explicit --pwd" in capsys.readouterr().out.lower()
        )

    def test_missing_dir_prompts_until_valid(self, tmp_path, monkeypatch):
        path = _make_store(tmp_path, str(tmp_path / "gone"))
        _fake_stdin(monkeypatch, tty=True)
        answers = iter(["not_a_real_dir", str(tmp_path)])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
        assert _resolve_missing_pwd(path, None) == str(tmp_path)

    def test_missing_dir_empty_answer_cancels(self, tmp_path, monkeypatch):
        path = _make_store(tmp_path, str(tmp_path / "gone"))
        _fake_stdin(monkeypatch, tty=True)
        monkeypatch.setattr("builtins.input", lambda _prompt: "")
        assert _resolve_missing_pwd(path, None) is False

    def test_resume_cli_cancel_never_enters_runtime(
        self, tmp_path, monkeypatch, capsys
    ):
        path = _make_store(tmp_path, str(tmp_path / "gone"))
        monkeypatch.setattr(resume_mod, "configure_utf8_stdio", lambda **_k: None)
        monkeypatch.setattr(resume_mod, "set_level", lambda *_a, **_k: None)
        monkeypatch.setattr(resume_mod, "_resolve_session", lambda *_a, **_k: path)
        monkeypatch.setattr(
            resume_mod, "announce_migration_if_needed", lambda *_a, **_k: None
        )
        monkeypatch.setattr(resume_mod, "_resolve_missing_pwd", lambda *_a: False)

        async def forbidden_run(*_args, **_kwargs):
            pytest.fail("cancel must not construct or run an engine")

        monkeypatch.setattr(resume_mod, "_run", forbidden_run)

        assert resume_mod.resume_cli("saved", None, "INFO") == 0
        assert "Resume cancelled." in capsys.readouterr().out

    def test_resume_failure_does_not_report_session_saved(
        self, tmp_path, monkeypatch, capsys
    ):
        path = _make_store(tmp_path, str(tmp_path))
        monkeypatch.setattr(resume_mod, "configure_utf8_stdio", lambda **_k: None)
        monkeypatch.setattr(resume_mod, "set_level", lambda *_a, **_k: None)
        monkeypatch.setattr(resume_mod, "_resolve_session", lambda *_a, **_k: path)
        monkeypatch.setattr(
            resume_mod, "announce_migration_if_needed", lambda *_a, **_k: None
        )
        monkeypatch.setattr(resume_mod, "_resolve_missing_pwd", lambda *_a: None)

        async def failed_run(*_args, **_kwargs):
            raise RuntimeError("resume failed")

        monkeypatch.setattr(resume_mod, "_run", failed_run)

        assert resume_mod.resume_cli("saved", None, "INFO") == 1
        output = capsys.readouterr().out
        assert "Error: resume failed" in output
        assert "Session saved" not in output

    def test_resume_success_still_reports_session_saved(
        self, tmp_path, monkeypatch, capsys
    ):
        path = _make_store(tmp_path, str(tmp_path))
        monkeypatch.setattr(resume_mod, "configure_utf8_stdio", lambda **_k: None)
        monkeypatch.setattr(resume_mod, "set_level", lambda *_a, **_k: None)
        monkeypatch.setattr(resume_mod, "_resolve_session", lambda *_a, **_k: path)
        monkeypatch.setattr(
            resume_mod, "announce_migration_if_needed", lambda *_a, **_k: None
        )
        monkeypatch.setattr(resume_mod, "_resolve_missing_pwd", lambda *_a: None)

        async def successful_run(*_args, **_kwargs):
            return 0

        monkeypatch.setattr(resume_mod, "_run", successful_run)

        assert resume_mod.resume_cli("saved", None, "INFO") == 0
        assert "Session saved" in capsys.readouterr().out

    async def test_declining_missing_image_confirmation_leaves_history_unchanged(
        self, tmp_path, monkeypatch, capsys
    ):
        path = _make_store(tmp_path, str(tmp_path))
        url = (tmp_path / "removed.png").as_uri()
        store = SessionStore(path)
        try:
            store.append_event(
                "a",
                "user_message",
                {"content": [{"type": "image_url", "image_url": {"url": url}}]},
                turn_index=1,
                branch_id=1,
            )
            store.append_event("a", "processing_end", {}, turn_index=1, branch_id=1)
        finally:
            store.close(update_status=False)
        _fake_stdin(monkeypatch, tty=True)
        prompts = []

        def decline(prompt):
            prompts.append(prompt)
            return ""

        monkeypatch.setattr("builtins.input", decline)
        assert await resume_mod._run(path, None, None, "tui") is None
        assert len(prompts) == 1 and "[y/N]" in prompts[0]
        output = capsys.readouterr()
        assert "1 original file(s)" in output.err and url in output.err
        assert "Resume cancelled." in output.out
        assert await resume_mod._run(path, None, None, "tui", "error") == 1
        assert len(prompts) == 1
