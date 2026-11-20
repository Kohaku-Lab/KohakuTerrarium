"""Unit tests for :mod:`kohakuterrarium.studio.identity.llm.profiles`.

Most functions are thin wrappers over ``kohakuterrarium.llm.profiles.resolve``.
We focus on pure helpers and the input-validation logic.
"""

import asyncio
import multiprocessing

import pytest
from fastapi import HTTPException

from kohakuterrarium.api.routes.identity.llm import ProfileRequest, create_profile
from kohakuterrarium.llm.discovery import DiscoveredModel
from kohakuterrarium.llm.presets.store import load_presets

from kohakuterrarium.studio.identity.llm.profiles import (
    get_profile_for_identifier,
    save_discovered_model,
    save_profile_record,
    split_identifier,
)


class TestSplitIdentifier:
    def test_bare_name(self):
        assert split_identifier("gpt-4o") == ("", "gpt-4o")

    def test_qualified(self):
        assert split_identifier("openai/gpt-4o") == ("openai", "gpt-4o")

    def test_multi_slash(self):
        # Only the first slash splits; rest stays in the name.
        assert split_identifier("a/b/c") == ("a", "b/c")


class TestSaveProfileRecord:
    def test_missing_name_raises(self):
        with pytest.raises(ValueError, match="required"):
            save_profile_record("", "model", "provider")

    def test_missing_model_raises(self):
        with pytest.raises(ValueError, match="required"):
            save_profile_record("name", "", "provider")

    def test_missing_provider_raises(self):
        with pytest.raises(ValueError, match="required"):
            save_profile_record("name", "model", "")

    def test_unknown_provider_raises(self, monkeypatch):
        from kohakuterrarium.studio.identity.llm import profiles as mod

        monkeypatch.setattr(mod, "load_backends", lambda: {})
        with pytest.raises(ValueError, match="Provider not found"):
            save_profile_record("name", "model", "ghost")


class TestGetProfileForIdentifier:
    def test_delegates_to_split_then_lookup(self, monkeypatch):
        from kohakuterrarium.studio.identity.llm import profiles as mod

        calls = {}

        def fake_get_profile(bare, provider):
            calls["bare"] = bare
            calls["provider"] = provider
            return "found"

        monkeypatch.setattr(mod, "get_profile", fake_get_profile)
        out = get_profile_for_identifier("openai/gpt-4o")
        assert out == "found"
        assert calls == {"bare": "gpt-4o", "provider": "openai"}


def _concurrent_catalog_writer(gate, index):
    # Independent processes use the same config directory as a CLI and GUI.
    gate.wait(timeout=20)
    for round_id in range(5):
        name = f"parallel-{round_id}-{index}"
        if index % 2:
            save_discovered_model("qwen", DiscoveredModel(name))
        else:
            asyncio.run(
                create_profile(
                    ProfileRequest(
                        name=name, model=name, provider="qwen", create_only=True
                    )
                )
            )


def test_catalog_concurrent_creates_preserve_every_preset():
    ctx = multiprocessing.get_context("spawn")
    gate = ctx.Barrier(4)
    children = [
        ctx.Process(target=_concurrent_catalog_writer, args=(gate, i)) for i in range(4)
    ]
    for child in children:
        child.start()
    try:
        for child in children:
            child.join(35)
            assert child.exitcode == 0
        expected = {("qwen", f"parallel-{r}-{i}") for r in range(5) for i in range(4)}
        assert expected <= set(load_presets())
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
            child.join()


async def test_concurrent_create_only_never_replaces_winning_model():
    models = [f"candidate-{i}" for i in range(4)]
    results = await asyncio.gather(
        *(
            create_profile(
                ProfileRequest(provider="qwen", name="race", model=m, create_only=True)
            )
            for m in models
        ),
        return_exceptions=True,
    )
    winners = [m for m, result in zip(models, results) if isinstance(result, dict)]
    assert winners == [load_presets()[("qwen", "race")].model]
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    assert [error.status_code for error in conflicts] == [409, 409, 409]
