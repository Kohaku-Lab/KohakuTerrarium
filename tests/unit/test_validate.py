"""Unit tests for :mod:`kohakuterrarium.validate` (+ ``kt doctor``).

The validators are the raise-instead-of-warn re-exposure of existing
logic (E4) — every test pins that failures RAISE typed errors and that
successes return real data.
"""

import pytest

from kohakuterrarium import validate
from kohakuterrarium.errors import (
    ConfigNotFoundError,
    LLMNotConfiguredError,
    PackageNotInstalledError,
)
from kohakuterrarium.cli.doctor import doctor_cli
from kohakuterrarium.llm.profile_types import LLMProfile
from kohakuterrarium.testing.llm import ScriptedLLM


def _write_creature(tmp_path, name="vtest"):
    (tmp_path / "config.yaml").write_text(
        f"name: {name}\n"
        "input:\n  type: none\n"
        "output:\n  type: none\n"
        "tools:\n  - name: bash\n",
        encoding="utf-8",
    )
    return tmp_path


class TestValidateConfig:
    def test_valid_folder_returns_config(self, tmp_path):
        cfg = validate.config(_write_creature(tmp_path))
        assert cfg.name == "vtest"

    def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(ConfigNotFoundError):
            validate.config(tmp_path / "ghost")

    def test_uninstalled_package_ref_raises(self, tmp_path, monkeypatch):
        from kohakuterrarium.packages import locations as loc_mod

        monkeypatch.setattr(loc_mod, "PACKAGES_DIR", tmp_path / "none")
        with pytest.raises(PackageNotInstalledError):
            validate.config("@ghost/creatures/x")

    def test_broken_base_config_raises(self, tmp_path):
        (tmp_path / "config.yaml").write_text(
            "name: x\nbase_config: creatures/ghost\n", encoding="utf-8"
        )
        with pytest.raises(ConfigNotFoundError, match="base_config"):
            validate.config(tmp_path)


class TestValidateLLM:
    def test_unresolvable_selector_raises(self, monkeypatch):
        import kohakuterrarium.validate as v_mod

        monkeypatch.setattr(v_mod, "resolve_controller_llm", lambda *a, **kw: None)
        with pytest.raises(LLMNotConfiguredError):
            validate.llm("ghost/selector")

    def test_no_default_raises_with_hint(self, monkeypatch):
        import kohakuterrarium.validate as v_mod

        monkeypatch.setattr(v_mod, "resolve_controller_llm", lambda *a, **kw: None)
        with pytest.raises(LLMNotConfiguredError, match="kt model default"):
            validate.llm(None)

    def test_resolvable_returns_identifier(self, monkeypatch):
        import kohakuterrarium.validate as v_mod

        profile = LLMProfile(
            name="p", model="gpt-4", provider="openai", backend_type="openai"
        )
        monkeypatch.setattr(v_mod, "resolve_controller_llm", lambda *a, **kw: profile)
        monkeypatch.setattr(v_mod, "_create_from_profile", lambda p: object())
        out = validate.llm("p")
        assert "gpt-4" in out or "p" in out


class TestValidateCreature:
    def test_dry_run_build_reports(self, tmp_path):
        report = validate.creature(
            _write_creature(tmp_path), llm_binding=ScriptedLLM(["x"])
        )
        assert report.name == "vtest"
        assert "bash" in report.tools
        assert report.config_path == str(tmp_path.resolve())

    def test_unknown_tool_fails_loudly(self, tmp_path):
        from kohakuterrarium.errors import ConfigError

        (tmp_path / "config.yaml").write_text(
            "name: bad\n"
            "input:\n  type: none\n"
            "output:\n  type: none\n"
            "tools:\n  - name: definitely_no_tool\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="Unknown built-in tool"):
            validate.creature(tmp_path, llm_binding=ScriptedLLM(["x"]))


class TestValidatePing:
    async def test_ping_round_trip_with_instance(self):
        reply = await validate.ping(ScriptedLLM(["pong"]))
        assert "pong" in reply


class TestDoctorCli:
    def test_doctor_all_green(self, tmp_path, monkeypatch, capsys):
        import kohakuterrarium.cli.doctor as doc_mod

        monkeypatch.setattr(doc_mod, "config_dir", lambda: tmp_path / "cfg")
        monkeypatch.setattr(doc_mod, "list_packages", lambda: [])
        monkeypatch.setattr(doc_mod, "get_default_model", lambda: "m")
        monkeypatch.setattr(doc_mod.validate, "llm", lambda sel: "prov/m")
        rc = doctor_cli()
        out = capsys.readouterr().out
        assert rc == 0
        assert "All checks passed." in out

    def test_doctor_red_on_llm_failure(self, tmp_path, monkeypatch, capsys):
        import kohakuterrarium.cli.doctor as doc_mod

        def _boom(sel):
            raise LLMNotConfiguredError("no profile")

        monkeypatch.setattr(doc_mod, "config_dir", lambda: tmp_path / "cfg")
        monkeypatch.setattr(doc_mod, "list_packages", lambda: [])
        monkeypatch.setattr(doc_mod, "get_default_model", lambda: "m")
        monkeypatch.setattr(doc_mod.validate, "llm", _boom)
        rc = doctor_cli()
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAILED" in out
        assert "no profile" in out

    def test_doctor_creature_target(self, tmp_path, monkeypatch, capsys):
        import kohakuterrarium.cli.doctor as doc_mod

        creature_dir = tmp_path / "agent"
        creature_dir.mkdir()
        _write_creature(creature_dir, name="doc-agent")
        monkeypatch.setattr(doc_mod, "config_dir", lambda: tmp_path / "cfg")
        monkeypatch.setattr(doc_mod, "list_packages", lambda: [])
        monkeypatch.setattr(doc_mod, "get_default_model", lambda: "m")
        monkeypatch.setattr(doc_mod.validate, "llm", lambda sel: "prov/m")
        # The creature dry-run hits the REAL validate.creature — config
        # parsing, tool registration, the lot.  Bind a ScriptedLLM so no
        # model resolution is needed.  (Capture the real function first
        # — the patched attribute IS ``validate.creature``.)
        real_creature = validate.creature
        monkeypatch.setattr(
            doc_mod.validate,
            "creature",
            lambda t: real_creature(t, llm_binding=ScriptedLLM(["x"])),
        )
        rc = doctor_cli(str(creature_dir))
        out = capsys.readouterr().out
        assert rc == 0
        assert "doc-agent" in out
