"""Fixtures for the Studio session tests: a creature folder and the LLM seam."""

import pytest

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.core import agent_compact as _agent_compact_mod
from kohakuterrarium.core import agent_model as _agent_model_mod
from kohakuterrarium.studio.sessions.summary import tracking as _summary_tracking_mod
from kohakuterrarium.testing.llm import ScriptedLLM


@pytest.fixture
def scripted(monkeypatch):
    """Patch every provider bind point; mutate ``["script"]`` to steer replies.

    ``["built"]`` lists each provider handed out, in order.
    """
    holder = {"script": ["OK"], "built": []}

    def _build(*_args, **_kwargs):
        llm = ScriptedLLM(holder["script"])
        holder["built"].append(llm)
        return llm

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _build)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _build)
    for module in (
        _bootstrap_llm_mod,
        _agent_model_mod,
        _agent_compact_mod,
        _summary_tracking_mod,
    ):
        monkeypatch.setattr(module, "create_llm_from_profile_name", _build)
    return holder


@pytest.fixture
def creature_dir(tmp_path):
    folder = tmp_path / "creatures" / "probe"
    folder.mkdir(parents=True)
    (folder / "config.yaml").write_text(
        "name: probe\nversion: '1.0'\nsystem_prompt: You are a test creature.\n"
        "tools: []\nsubagents: []\n",
        encoding="utf-8",
    )
    return folder
