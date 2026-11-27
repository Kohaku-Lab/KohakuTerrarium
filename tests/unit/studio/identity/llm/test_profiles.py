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
from kohakuterrarium.llm.presets.drafts import catalog_rows
from kohakuterrarium.llm.profiles.resolve import list_all
from kohakuterrarium.llm.presets.store import load_presets
from kohakuterrarium.llm.profiles.backends import load_yaml_store, save_yaml_store

from kohakuterrarium.studio.identity.llm.profiles import (
    get_profile_for_identifier,
    list_profiles_payload,
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


class TestContextWindowOnSave:
    def test_editor_save_without_the_field_keeps_the_stored_window(self):
        save_profile_record(
            "wide", "m", "openai", max_context=300000, context_window=400000
        )
        save_profile_record("wide", "m", "openai", max_context=350000)
        (preset,) = [
            p for key, p in load_presets().items() if key == ("openai", "wide")
        ]
        assert (preset.max_context, preset.context_window) == (350000, 400000)

    def test_overriding_a_builtin_keeps_its_window(self):
        save_profile_record("gpt-6-astra", "gpt-6-astra", "codex", max_context=500000)
        preset = load_presets()[("codex", "gpt-6-astra")]
        assert (preset.max_context, preset.context_window) == (500000, 1000000)

    def test_discovered_model_saves_its_draft_fields(self):
        model = DiscoveredModel(
            "gpt-6.9-sol",
            max_context=500000,
            reasoning_levels=("low", "medium", "high"),
        )
        preset, created = save_discovered_model("codex", model)
        assert created
        assert preset.context_window == 500000
        assert preset.max_context == 500000
        assert list(preset.variation_groups["reasoning"]) == ["low", "medium", "high"]
        assert preset.reasoning_effort == "medium"
        stored = load_presets()[("codex", "gpt-6.9-sol")]
        assert stored.variation_groups == preset.variation_groups


def test_catalog_name_collision_reuses_the_users_actual_route_without_writing():
    save_profile_record("fly-model", "private-route", "qwen", max_context=262144)
    save_profile_record("alias", "fly/model", "qwen")
    before = load_yaml_store()
    model = DiscoveredModel("fly/model", max_context=10000)
    (row,) = catalog_rows("qwen", [model])
    assert row["has_preset"]
    assert (row["preset"]["name"], row["preset"]["model"]) == (
        "fly-model",
        "private-route",
    )
    assert "context_window" not in row["preset"]
    reused, created = save_discovered_model("qwen", model)
    assert (created, reused.name, reused.model, reused.max_context) == (
        False,
        "fly-model",
        "private-route",
        262144,
    )
    assert load_yaml_store() == before


def test_shadowed_builtin_routes_to_user_definition_on_both_surfaces():
    save_profile_record("gpt-6-astra", "private-astra", "codex", max_context=262144)
    model = DiscoveredModel("gpt-6-astra")
    (row,) = catalog_rows("codex", [model])
    assert row["has_preset"] and row["preset"]["model"] == "private-astra"
    preset, created = save_discovered_model("codex", model)
    assert not created and preset.model == "private-astra"


def test_catalog_lists_scoped_alias_choices_and_refuses_an_implicit_choice():
    save_profile_record("fast", "private-raw", "openai", temperature=0.5)
    save_profile_record("careful", "private-raw", "openai", temperature=0.1)
    save_profile_record("other", "private-raw", "anthropic")
    before = load_yaml_store()
    model = DiscoveredModel("private-raw")
    (row,) = catalog_rows("openai", [model])
    assert row["has_preset"]
    assert row["preset_choices"] == ["careful", "fast"]
    assert row["preset"]["name"] == "private-raw"
    with pytest.raises(ValueError, match="openai/careful, openai/fast"):
        save_discovered_model("openai", model)
    assert load_yaml_store() == before


def test_unresolved_user_preset_stays_in_inventories_until_provider_returns():
    data = {
        "presets": {
            "offline": {"custom": {"model": "fly/model", "max_context": 262144}}
        }
    }
    save_yaml_store(data)
    for rows in (list_profiles_payload(), list_all()):
        (mine,) = [r for r in rows if r["provider"] == "offline"]
        assert mine["provider_missing"] and not mine["available"]
        assert (mine["name"], mine["model"], mine["max_context"]) == (
            "custom",
            "fly/model",
            262144,
        )
    assert get_profile_for_identifier("offline/custom") is None
    assert load_yaml_store() == data
    save_yaml_store({**data, "backends": {"offline": {"backend_type": "openai"}}})
    assert get_profile_for_identifier("offline/custom").max_context == 262144
    assert not next(r for r in list_all() if r["provider"] == "offline").get(
        "provider_missing"
    )


class TestLegacyContextGroupOnSave:
    GROUPS = {
        "context": {"200k": {"max_context": 200000}, "long": {"max_context": 900000}},
        "reasoning": {"high": {"reasoning_effort": "high"}},
    }

    def _store_legacy(self):
        legacy = {"model": "m", "max_context": 200000, "variation_groups": self.GROUPS}
        save_yaml_store({"version": 3, "presets": {"openai": {"cc": legacy}}})

    def test_payload_shows_the_folded_preset(self):
        self._store_legacy()
        (row,) = [p for p in list_profiles_payload() if p["name"] == "cc"]
        assert row["variation_groups"] == {"reasoning": self.GROUPS["reasoning"]}
        assert row["context_window"] == 900000

    def test_editor_save_keeps_the_stored_group_and_its_aliases(self):
        self._store_legacy()
        shown = {"reasoning": self.GROUPS["reasoning"]}
        save_profile_record(
            "cc", "m", "openai", max_output=4096, variation_groups=shown
        )
        stored = load_yaml_store()["presets"]["openai"]["cc"]
        assert stored["variation_groups"] == self.GROUPS
        assert "context_window" not in stored
        assert get_profile_for_identifier("openai/cc@context=long").max_context == (
            900000
        )

    def test_editor_save_that_drops_a_group_keeps_the_aliases(self):
        self._store_legacy()
        save_profile_record("cc", "m", "openai", variation_groups={})
        stored = load_yaml_store()["presets"]["openai"]["cc"]
        assert "variation_groups" not in stored
        assert stored["context_aliases"] == {"long": 900000}
        assert get_profile_for_identifier("openai/cc@long").max_context == 900000
