"""Session one-line summary settings — ``session-summary.json`` in the config dir.

``source`` picks how summaries are written (``llm`` / ``compaction`` /
``heuristic`` / ``off``), ``every_n_turns`` how often an automatic summary is
refreshed after the first turn, and ``model`` an LLM profile that replaces the
session's own model for the ``llm`` source. ``KT_SESSION_SUMMARY_SOURCE``
overrides ``source`` for the process.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kohakuterrarium.utils.config_dir import config_dir

LLM = "llm"
COMPACTION = "compaction"
HEURISTIC = "heuristic"
OFF = "off"
SOURCES = (LLM, COMPACTION, HEURISTIC, OFF)
SOURCE_ENV = "KT_SESSION_SUMMARY_SOURCE"


@dataclass(frozen=True)
class SummarySettings:
    source: str = LLM
    every_n_turns: int = 5
    model: str = ""


def settings_path() -> Path:
    return config_dir() / "session-summary.json"


def parse_settings(raw: Any) -> SummarySettings:
    """Validate a mapping into settings; raises ``ValueError`` on a bad field."""
    raw = raw if isinstance(raw, dict) else {}
    source = str(raw.get("source", LLM) or LLM)
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    every = raw.get("every_n_turns", 5)
    if isinstance(every, bool) or not isinstance(every, int) or every < 1:
        raise ValueError("every_n_turns must be an integer >= 1")
    model = raw.get("model", "") or ""
    if not isinstance(model, str):
        raise ValueError("model must be a string")
    return SummarySettings(source=source, every_n_turns=every, model=model.strip())


def load_settings(*, apply_env: bool = True) -> SummarySettings:
    """Stored settings, with the env override unless ``apply_env`` is off.

    A missing or bad file reads as defaults.
    """
    try:
        settings = parse_settings(json.loads(settings_path().read_text("utf-8")))
    except (OSError, ValueError):
        settings = SummarySettings()
    override = os.environ.get(SOURCE_ENV, "").strip()
    if apply_env and override in SOURCES:
        settings = SummarySettings(override, settings.every_n_turns, settings.model)
    return settings


def save_settings(values: dict[str, Any]) -> SummarySettings:
    """Merge ``values`` over the stored settings, validate and persist."""
    try:
        stored = json.loads(settings_path().read_text("utf-8"))
    except (OSError, ValueError):
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    settings = parse_settings({**asdict(SummarySettings()), **stored, **values})
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    return settings
