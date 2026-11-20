"""Validated, model-specific action catalogs shared by Pet and agent playback."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).absolute().parent.parent
BUILTIN_SETS = json.loads((ROOT / "ui/action-sets.json").read_text(encoding="utf-8"))[
    "sets"
]
STATES = {"running", "waiting", "ready", "failed"}
PLAYBACK_TYPES = {"preset", "pose", "expression", "motion"}
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def validate_action_set(value: dict, model: str) -> dict:
    """Return a detached catalog or reject malformed and unbounded playback."""
    if not isinstance(value, dict) or set(value) != {
        "version",
        "model",
        "label",
        "defaults",
        "actions",
    }:
        raise ValueError(
            "An action set needs version, model, label, defaults and actions."
        )
    if value["version"] != 1 or isinstance(value["version"], bool):
        raise ValueError("Unsupported action-set version.")
    if value["model"] != model or model not in BUILTIN_SETS:
        raise ValueError("The action set belongs to a different model.")
    if not isinstance(value["label"], str) or not 1 <= len(value["label"]) <= 100:
        raise ValueError("The action-set label must contain 1–100 characters.")
    actions, defaults = value["actions"], value["defaults"]
    if not isinstance(actions, dict) or not 1 <= len(actions) <= 64:
        raise ValueError("An action set supports 1–64 actions.")
    if not isinstance(defaults, dict) or set(defaults) != STATES:
        raise ValueError("Map running, waiting, ready and failed to action IDs.")
    for name in defaults.values():
        if not isinstance(name, str) or name not in actions:
            raise ValueError("A default refers to a missing action.")
    for name, action in actions.items():
        if not isinstance(name, str) or not ID.fullmatch(name):
            raise ValueError(
                "Action IDs use lowercase letters, digits, underscores and hyphens."
            )
        if not isinstance(action, dict) or set(action) != {
            "description",
            "seconds",
            "maxSeconds",
            "playback",
        }:
            raise ValueError(f"Invalid action definition: {name}.")
        description = action["description"]
        if not isinstance(description, str) or not 1 <= len(description) <= 240:
            raise ValueError("Action descriptions must contain 1–240 characters.")
        seconds, maximum = action["seconds"], action["maxSeconds"]
        if any(type(n) not in {int, float} for n in (seconds, maximum)) or not (
            1 <= seconds <= maximum <= 15
        ):
            raise ValueError(
                "Action durations must satisfy 1 <= seconds <= maxSeconds <= 15."
            )
        playback = action["playback"]
        if (
            not isinstance(playback, dict)
            or not isinstance(playback.get("type"), str)
            or playback["type"] not in PLAYBACK_TYPES
        ):
            raise ValueError("Playback must be preset, pose, expression or motion.")
        kind = playback["type"]
        field = "parameters" if kind == "pose" else "name"
        if set(playback) != {"type", field}:
            raise ValueError("Unexpected playback fields.")
        if kind == "pose":
            parameters = playback[field]
            if not isinstance(parameters, dict) or not 1 <= len(parameters) <= 32:
                raise ValueError("A pose supports 1–32 parameters.")
            for key, number in parameters.items():
                if not isinstance(key, str) or not 1 <= len(key) <= 100:
                    raise ValueError("Invalid parameter ID.")
                if type(number) not in {int, float} or not -1000 <= number <= 1000:
                    raise ValueError(
                        "Pose values must be finite numbers between -1000 and 1000."
                    )
        elif (
            not isinstance(playback[field], str) or not 1 <= len(playback[field]) <= 100
        ):
            raise ValueError("Playback needs a model capability name.")
        elif kind == "preset" and playback[field] not in STATES:
            raise ValueError("Unknown built-in pose preset.")
    return json.loads(json.dumps(value, allow_nan=False))


ACTION_SETS = {
    model: validate_action_set(value, model) for model, value in BUILTIN_SETS.items()
}
