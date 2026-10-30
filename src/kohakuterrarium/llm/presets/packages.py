"""Fold installed packages' ``llm_presets:`` into the built-in preset catalogue."""

from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def merge_package_presets(
    packages: list[dict[str, Any]],
    builtin_keys: set[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Valid package presets keyed by ``(provider, name)``; built-ins and
    earlier packages win over later duplicates."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for pkg in packages:
        for preset in pkg.get("llm_presets", []) or []:
            if not isinstance(preset, dict):
                continue
            preset_name = preset.get("name")
            provider = preset.get("provider", "")
            if not preset_name or not provider:
                continue
            key = (provider, preset_name)
            if key in builtin_keys or key in merged:
                logger.debug(
                    "Package preset skipped",
                    reason="builtin exists" if key in builtin_keys else "duplicate",
                    preset=preset_name,
                    provider=provider,
                    package=pkg.get("name", "?"),
                )
                continue
            merged[key] = {
                k: v for k, v in preset.items() if k not in {"name", "provider"}
            }
    return merged
