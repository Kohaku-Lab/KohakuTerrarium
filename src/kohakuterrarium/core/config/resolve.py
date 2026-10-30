"""Resolve creature-level settings that accept a fixed set of values."""

from typing import Any

from kohakuterrarium.errors import ConfigError
from kohakuterrarium.modules.tool.doc_mode import DEFAULT_DOC_MODE, validate_doc_mode
from kohakuterrarium.utils.file_guard import PWD_GUARD_MODES


def resolve_tool_doc_mode(
    controller_data: dict[str, Any], config_data: dict[str, Any]
) -> str:
    """Resolve the creature-level documentation tier, rejecting ``skill_mode``."""
    for scope in (controller_data, config_data):
        if "skill_mode" in scope:
            raise ConfigError(
                "'skill_mode' was replaced by 'tool_doc_mode'.\n"
                "  dynamic -> standard   (the default; you can delete the key)\n"
                "  static  -> full\n"
                "Per-tool override: "
                "tools: [{name: x, type: builtin, doc_mode: full}]"
            )
    value = controller_data.get(
        "tool_doc_mode", config_data.get("tool_doc_mode", DEFAULT_DOC_MODE)
    )
    return validate_doc_mode(str(value), where="tool_doc_mode")


def resolve_pwd_guard(
    controller_data: dict[str, Any], config_data: dict[str, Any]
) -> str:
    """Resolve the working-directory guard mode, rejecting unknown values."""
    raw = controller_data.get("pwd_guard", config_data.get("pwd_guard", "warn"))
    # An unquoted ``off`` in YAML parses as the boolean False.
    value = "off" if raw is False else str(raw)
    if value not in PWD_GUARD_MODES:
        raise ConfigError(
            f"pwd_guard must be one of {', '.join(PWD_GUARD_MODES)}; got {value!r}"
        )
    return value
