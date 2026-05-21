"""Write helpers for the ``[auth]`` section of ``<config_dir>/config.toml``.

The stdlib ``tomllib`` is read-only — there is no built-in writer.
This module emits a minimal subset of TOML (strings, ints, bools,
lists) sufficient for the ``[auth]`` shapes we control.  It is the
ONE place both the CLI (``kt admin set-host-token``) and the API
admin-rotation routes write secrets to disk, so the wire format
cannot drift between the two callers.
"""

from pathlib import Path

import tomllib

from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def config_toml_path() -> Path:
    return config_dir() / "config.toml"


def read_config_toml() -> dict:
    """Read ``<config_dir>/config.toml``; empty dict if absent."""
    path = config_toml_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(
            "config.toml unreadable; treating as empty",
            path=str(path),
            error=str(e),
        )
        return {}


def write_auth_section(updates: dict[str, object]) -> None:
    """Merge ``updates`` into the ``[auth]`` table and rewrite the file.

    Existing sections + values are preserved; only the ``[auth]``
    section is mutated.  Output is sorted within each section for
    stable diffs.
    """
    path = config_toml_path()
    data = read_config_toml()
    auth = dict(data.get("auth") or {})
    auth.update(updates)
    data["auth"] = auth
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_toml_like(data), encoding="utf-8")


def _dump_toml_like(data: dict) -> str:
    """Minimal TOML emitter for the framework's ``[section]`` shapes.

    Supports:

    - String / int / float / bool scalars.
    - Flat lists of the above.
    - One level of ``[section]`` tables (insertion-ordered; keys
      within each section sorted alphabetically for stable diffs).

    **Raises** :class:`ValueError` rather than silently losing data
    on either of:

    - Top-level scalars outside any section.  The framework defines
      none; an operator who manually adds one is using TOML shapes
      this writer doesn't preserve.  The re-audit caught the earlier
      log-warning-and-drop behaviour as "loss-with-warning that the
      operator probably won't see" — making this an explicit raise
      matches the nested-table policy and the "fail loudly, don't
      corrupt user data" principle.
    - Nested tables (``[section.subsection]``).

    If real nested-table / top-level-scalar use cases appear, swap
    this for a proper TOML writer (e.g. ``tomli_w``) — the
    ``[auth]`` use case didn't justify the dependency.
    """
    top_level_scalars: list[str] = []
    lines: list[str] = []
    for section, body in data.items():
        if not isinstance(body, dict):
            top_level_scalars.append(section)
            continue
        lines.append(f"[{section}]")
        for key in sorted(body):
            value = body[key]
            if isinstance(value, dict):
                raise ValueError(
                    f"_dump_toml_like cannot emit nested tables; "
                    f"got {section}.{key} as a dict.  This minimal "
                    f"writer only supports one level of sections; "
                    f"swap in tomli_w if nested tables become a real "
                    f"requirement."
                )
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    if top_level_scalars:
        raise ValueError(
            "_dump_toml_like cannot emit top-level scalars (the "
            "framework's config.toml uses only [section] tables); "
            f"got: {', '.join(top_level_scalars)}.  Move these into "
            "a [section] before persisting, or swap in a proper "
            "TOML writer."
        )
    return "\n".join(lines).rstrip() + "\n"


def _toml_value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return f'"{v}"'


__all__ = ["config_toml_path", "read_config_toml", "write_auth_section"]
