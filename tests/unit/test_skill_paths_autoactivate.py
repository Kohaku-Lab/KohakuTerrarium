"""Unit tests for the ``paths:`` auto-activate injection pipeline."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.controller import Controller, ControllerConfig
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.skills import Skill, SkillPathScanner, SkillRegistry
from kohakuterrarium.skills.hints import inject_skill_path_hint


def _skill(name: str, **kw) -> Skill:
    return Skill(
        name=name,
        description=kw.pop("description", name),
        body=kw.pop("body", ""),
        origin=kw.pop("origin", "user"),
        **kw,
    )


def _agent_stub(reg: SkillRegistry, cwd: Path):
    scanner = SkillPathScanner()
    controller = SimpleNamespace(_pending_injections=[])
    executor = SimpleNamespace(_working_dir=str(cwd))
    return SimpleNamespace(
        skills=reg,
        skill_path_scanner=scanner,
        controller=controller,
        executor=executor,
    )


def test_inject_noop_when_registry_empty(tmp_path):
    agent = _agent_stub(SkillRegistry(), tmp_path)
    inject_skill_path_hint(agent)
    assert agent.controller._pending_injections == []


def test_inject_noop_when_no_file_matches(tmp_path):
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_skill("pdf", paths=["*.pdf"]))
    agent = _agent_stub(reg, tmp_path)
    inject_skill_path_hint(agent)
    assert agent.controller._pending_injections == []


def test_inject_adds_user_context_message_on_match(tmp_path):
    (tmp_path / "report.pdf").write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_skill("pdf-merge", paths=["*.pdf"]))
    agent = _agent_stub(reg, tmp_path)
    inject_skill_path_hint(agent)
    assert len(agent.controller._pending_injections) == 1
    msg = agent.controller._pending_injections[0]
    assert msg["role"] == "user"
    assert "pdf-merge" in msg["content"]


def test_inject_respects_invocation_blocked(tmp_path):
    (tmp_path / "doc.pdf").write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_skill("hidden", paths=["*.pdf"], disable_model_invocation=True))
    agent = _agent_stub(reg, tmp_path)
    inject_skill_path_hint(agent)
    # Matched skills with disable-model-invocation should not produce a hint.
    assert agent.controller._pending_injections == []


def test_inject_safe_when_controller_missing(tmp_path):
    (tmp_path / "report.pdf").write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_skill("pdf", paths=["*.pdf"]))
    agent = SimpleNamespace(
        skills=reg,
        skill_path_scanner=SkillPathScanner(),
        controller=None,
        executor=SimpleNamespace(_working_dir=str(tmp_path)),
    )
    inject_skill_path_hint(agent)  # must not raise


def test_inject_safe_when_registry_missing(tmp_path):
    agent = SimpleNamespace(
        skills=None,
        skill_path_scanner=SkillPathScanner(),
        controller=SimpleNamespace(_pending_injections=[]),
        executor=SimpleNamespace(_working_dir=str(tmp_path)),
    )
    inject_skill_path_hint(agent)  # must not raise
    assert agent.controller._pending_injections == []


class CaptureLLM:
    model = "capture-test"

    def __init__(self):
        self.calls = []

    @property
    def last_usage(self):
        return {}

    @property
    def last_assistant_content_parts(self):
        return None

    async def chat(self, messages, **kwargs):
        self.calls.append(list(messages))
        yield "ok"


@pytest.mark.asyncio
async def test_path_hint_keeps_single_system_message_in_llm_payload(tmp_path):
    (tmp_path / "report.pdf").write_text("x", encoding="utf-8")
    reg = SkillRegistry()
    reg.add(_skill("pdf-merge", body="Merge PDFs safely.", paths=["*.pdf"]))
    llm = CaptureLLM()
    controller = Controller(
        llm,
        ControllerConfig(
            system_prompt="sys",
            include_job_status=False,
            include_tools_list=False,
        ),
    )
    agent = SimpleNamespace(
        skills=reg,
        skill_path_scanner=SkillPathScanner(),
        controller=controller,
        executor=SimpleNamespace(_working_dir=str(tmp_path)),
    )

    inject_skill_path_hint(agent)
    await controller.push_event(create_user_input_event("merge these pdfs"))
    _ = [event async for event in controller.run_once()]

    roles = [message["role"] for message in llm.calls[0]]
    assert roles == ["system", "user", "user"]
    assert roles.count("system") == 1
    assert "Skill Context" in llm.calls[0][1]["content"]
