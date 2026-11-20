"""Action catalogs reject invalid playback before replacing a live binding."""

from copy import deepcopy

import pytest

from examples.packages.live2d_pet.kt_live2d.actions import (
    ACTION_SETS,
    validate_action_set,
)


def test_action_catalog_validation():
    for model, original in ACTION_SETS.items():
        catalog = validate_action_set(original, model)
        catalog["actions"]["nod"]["description"] = "Custom acknowledgement"
        assert original["actions"]["nod"]["description"] != "Custom acknowledgement"
        for change in [
            {"version": True},
            {"model": "other"},
            {"unexpected": 1},
            {"defaults": {"running": "missing"}},
            {"actions": {}},
        ]:
            with pytest.raises(ValueError):
                validate_action_set({**original, **change}, model)
        for playback in [
            {"type": [], "name": "Idle"},
            {"type": "pose", "parameters": {"ParamAngleX": float("nan")}},
            {"type": "pose", "parameters": {"ParamAngleX": True}},
            {"type": "pose", "parameters": {"ParamAngleX": 1001}},
            {"type": "motion", "name": "Idle", "url": "https://example.invalid"},
            {"type": "preset", "name": "missing"},
            {"type": "pose", "parameters": {}},
            {"type": "unknown", "name": "Idle"},
        ]:
            broken = deepcopy(original)
            broken["actions"]["nod"]["playback"] = playback
            with pytest.raises(ValueError):
                validate_action_set(broken, model)
        for duration in [False, float("nan"), float("inf"), -1, 16, "2"]:
            broken = deepcopy(original)
            broken["actions"]["nod"]["seconds"] = duration
            with pytest.raises(ValueError):
                validate_action_set(broken, model)
