"""The ``/model`` picker's data: KT presets as kohakutui picker items, answers as selectors.

Items are grouped by provider (in the order providers first appear), open on the creature's
current model with its variations preselected, and carry every variation group as picker
``choices`` in definition order, so ←/→ pick ``reasoning=high`` the way kt-cli's picker does.
A group the preset gives no default gets a leading ``default`` option, preselected: choosing it
leaves the group out of the selector, so the provider's own default applies. The answer becomes a
``provider/name@group=option,…`` selector for the ``/model`` command.
"""

from typing import Any

from kohakuterrarium.llm.profiles.variations import parse_variation_selector

DEFAULT = "default"


def model_items(presets: list[dict[str, Any]], current: str) -> list[dict[str, Any]]:
    """Picker items for ``presets``; ``current`` is the agent's ``provider/name@…`` id."""
    try:
        current_base, current_choices = parse_variation_selector(current or "")
    except ValueError:
        current_base, current_choices = current or "", {}

    by_provider: dict[str, list[dict[str, Any]]] = {}
    for preset in presets:
        provider = str(preset.get("provider") or preset.get("login_provider") or "")
        by_provider.setdefault(provider, []).append(preset)

    items: list[dict[str, Any]] = []
    for provider, group in by_provider.items():
        for preset in group:
            name = str(preset.get("name", ""))
            key = f"{provider}/{name}" if provider else name
            is_current = key == current_base or (not provider and name == current_base)
            variations = preset.get("variation_groups") or {}
            defaults = dict(preset.get("selected_variations") or {})
            chosen = dict(defaults)
            if is_current:
                chosen.update(current_choices)
            choices = []
            for group_name, options in sorted(variations.items()):
                if not options:
                    continue
                names = list(options)
                if group_name not in defaults:
                    names.insert(0, DEFAULT)
                    chosen.setdefault(group_name, DEFAULT)
                choices.append([group_name, names])
            model = str(preset.get("model", "") or "")
            items.append(
                {
                    "label": name,
                    "value": key,
                    "group": provider or None,
                    "detail": model if model and model != name else None,
                    "current": is_current,
                    "available": bool(preset.get("available", True)),
                    "choices": choices,
                    "chosen": chosen,
                }
            )
    return items


def selector_of(answer: Any) -> str:
    """The ``/model`` argument for a picker answer (plain value or value with choices)."""
    if isinstance(answer, dict):
        base = str(answer.get("value", ""))
        choices = answer.get("choices") or {}
        parts = [
            f"{group}={option}"
            for group, option in sorted(choices.items())
            if option and option != DEFAULT
        ]
        return f"{base}@{','.join(parts)}" if parts else base
    return str(answer or "")


def model_id(agent: Any) -> str:
    """The agent's ``provider/name@variations`` id (what ``/model`` accepts back)."""
    ident = getattr(agent, "llm_identifier", None)
    try:
        value = ident() if callable(ident) else ""
    except Exception:
        value = ""
    if value:
        return str(value)
    return str(getattr(getattr(agent, "config", None), "model", "") or "")
