"""Remembered ktui preferences, stored as ``ktui.json`` in KT's config directory.

Every change made in the UI (``/theme``, ``/renderer``) is saved at once; command-line flags
override the file for one run without writing it.
"""

import os
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from kohakutui import THEMES

from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

RENDERERS = ("inline", "hybrid", "fullscreen")
VERBOSITY = ("low", "medium", "high")
# auto: OSC 8 hyperlinks on a terminal; text writes "name (url)" for terminals that drop OSC 8
LINKS = ("auto", "osc8", "text")


@dataclass
class KtuiConfig:
    """``renderer``: inline, hybrid (inline chat, full-screen panels) or fullscreen."""

    renderer: str = "hybrid"
    theme: str = "kohaku-dark"
    verbosity: str = "medium"
    links: str = "auto"
    # full-screen panels ("rail", "side"): {"width": int, "collapsed": bool}
    panes: dict = field(default_factory=dict)

    @staticmethod
    def default_path() -> Path:
        return config_dir() / "ktui.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "KtuiConfig":
        """The saved preferences; defaults for a missing, unreadable or invalid file."""
        target = path or cls.default_path()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.warning("ktui config unreadable", path=str(target), error=str(exc))
            return cls()

        known = {f.name for f in fields(cls)}
        values = raw if isinstance(raw, dict) else {}
        config = cls(**{k: v for k, v in values.items() if k in known})
        return config.normalized()

    def normalized(self) -> "KtuiConfig":
        """Replaces unknown choices with the defaults."""
        if self.renderer not in RENDERERS:
            self.renderer = "hybrid"
        if self.theme not in THEMES:
            self.theme = "kohaku-dark"
        if self.verbosity not in VERBOSITY:
            self.verbosity = "medium"
        if self.links not in LINKS:
            self.links = "auto"
        if not isinstance(self.panes, dict):
            self.panes = {}
        self.panes = {
            name: {
                "width": (
                    int(p.get("width", 0)) if str(p.get("width", "")).isdigit() else 0
                ),
                "collapsed": bool(p.get("collapsed", False)),
            }
            for name, p in self.panes.items()
            if isinstance(p, dict)
        }
        return self

    def save(self, path: Path | None = None) -> None:
        """Writes the file atomically; failures are logged, never raised into the UI."""
        target = path or self.default_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            logger.warning("ktui config not saved", path=str(target), error=str(exc))
