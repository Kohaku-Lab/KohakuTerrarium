"""kt.ui inputs, buttons and forms as kohakutui controls.

Each handler draws into a ``ui_slot.Slot`` and talks to its block through ``slot.ui`` (an
``inline_ui.BlockUi``): ``value(name)`` reads state, ``bind(node, name)`` makes a control
write it, ``on_select(node, callback)`` runs a button, ``live`` says whether the block takes
input (closed and not frozen; a block still streaming or frozen draws its controls static).
"""

import asyncio
import time
from typing import Any

from kohakutui.ui import Button, Field, Stack, Text

from kohakuterrarium.builtins.kt_ui.expr import ExprError, evaluate, interpolate
from kohakuterrarium.builtins.kt_ui.values import to_display, truthy
from kohakuterrarium.builtins.ktui.ui_values import number, text, value

CONTROLS = {
    "number": "number",
    "slider": "slider",
    "text_input": "text",
    "select": "select",
    "radio": "radio",
    "toggle": "toggle",
    "checkbox": "checkbox",
    "date": "date",
    "rating": "rating",
}


def _options(raw: Any, scope: dict[str, Any]) -> list[dict[str, Any]]:
    got = value(raw, scope)
    if not isinstance(got, list):
        return []
    options = []
    for item in got:
        if isinstance(item, dict):
            options.append(
                {
                    "label": to_display(item.get("label", item.get("value"))),
                    "value": item.get("value", item.get("label")),
                }
            )
        else:
            options.append({"label": to_display(item), "value": item})
    return options


def control(slot: Any, node: dict, scope: dict) -> None:
    """One input bound to a state entry."""
    ui = slot.ui
    name = node.get("bind")
    kind = CONTROLS[node["type"]]
    current = ui.value(name)
    if kind in ("toggle", "checkbox"):
        current = truthy(current)
    elif kind == "rating" and current is None:
        current = 0
    spec = Field(
        control=kind,
        label=text(node.get("label"), scope),
        value=current,
        options=_options(node.get("options"), scope) or None,
        min=number(node.get("min"), scope),
        max=number(node.get("max"), scope),
        step=number(node.get("step"), scope),
        placeholder=text(node.get("placeholder"), scope) or None,
        disabled=not ui.live,
    )
    drawn = slot.add(spec)
    if ui.live and isinstance(name, str):
        ui.bind(drawn.node, name)


def resolve_deep(raw: Any, scope: dict) -> Any:
    """``data`` with every string's templates evaluated, nested lists and maps included; a
    failing expression gives None (a tool gets data, not the display placeholder "?").
    """
    if isinstance(raw, dict):
        return {k: resolve_deep(v, scope) for k, v in raw.items()}
    if isinstance(raw, list):
        return [resolve_deep(v, scope) for v in raw]
    if isinstance(raw, str):
        try:
            return interpolate(raw, scope)
        except ExprError:
            return None
    return raw


def bound_names(node: Any) -> list[str]:
    """The state entries the inputs under ``node`` write."""
    names: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        if isinstance(current.get("bind"), str):
            names.append(current["bind"])
        stack.extend(current.get("children") or [])
        stack.append(current.get("child"))
    return names


def button(slot: Any, node: dict, scope: dict) -> None:
    """``set`` changes state; then ``message`` is said as the user or, in a tool's widget,
    ``send`` posts the named widget message with ``data``. Disabled draws it muted."""
    ui = slot.ui
    label = text(node.get("label"), scope) or "OK"
    disabled = truthy(value(node.get("disabled", False), scope))
    if disabled or not ui.live:
        slot.add(Text(f"[ {label} ]", tone="muted" if disabled else "accent"))
        return
    hint = "enter · careful" if node.get("style") == "danger" else "enter"
    drawn = slot.add(Button(label=f"[ {label} ]", focused_hint=hint))
    setmap = node.get("set") if isinstance(node.get("set"), dict) else {}

    def press() -> None:
        changes = {}
        for key, expr in setmap.items():
            try:
                changes[key] = evaluate(expr, scope)
            except ExprError:
                continue
        if changes:
            ui.set_state(changes)
        after = {**scope, **changes}
        # as on the web: ``message`` (what markup ``send=`` becomes) is said as the user;
        # ``send`` names a widget message, which an inline block has nowhere to send
        if isinstance(node.get("message"), str):
            ui.send(text(node["message"], after))
        elif ui.widget and isinstance(node.get("send"), str):
            data = resolve_deep(node.get("data") or {}, after)
            ui.message(node["send"], data if isinstance(data, dict) else {})

    ui.on_select(drawn.node, press)


def form(slot: Any, node: dict, scope: dict) -> None:
    """Its inputs, then one submit button: ``message`` said as the user, or (a tool's
    widget) the ``send`` widget message carrying the bound values and ``data``."""
    ui = slot.ui
    box = slot.add(Stack(gap=0))
    for child in node.get("children") or []:
        ui.mount(box, child, scope)
    submit = {
        "type": "button",
        "label": node.get("submit") or "Submit",
        "message": node.get("message"),
        "send": node.get("send"),
    }
    if ui.widget and not isinstance(node.get("message"), str):
        data = {name: scope.get(name) for name in bound_names(node)}
        extra = resolve_deep(node.get("data") or {}, scope)
        submit["data"] = {**data, **(extra if isinstance(extra, dict) else {})}
    button(box, submit, scope)


def _clock(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def timer(slot: Any, node: dict, scope: dict) -> None:
    """A countdown or stopwatch with start / pause and reset, as the web's timer; it ticks
    once a second while running. Its time survives the block being drawn again."""
    ui = slot.ui
    countdown = node.get("mode", "countdown") != "stopwatch"
    total = float(number(node.get("seconds"), scope) or 0)
    label = text(node.get("label"), scope)
    state = ui.remembered(f"timer:{id(node)}", None)
    if state is None:
        state = {"before": 0.0, "since": None, "drawn": None}
        ui.block.view_state[f"timer:{id(node)}"] = state
        if truthy(value(node.get("autostart", False), scope)) and ui.live:
            state["since"] = time.monotonic()

    def elapsed() -> float:
        since = state["since"]
        return state["before"] + (
            time.monotonic() - since if since is not None else 0.0
        )

    def shown() -> tuple[str, bool]:
        spent = elapsed()
        if countdown:
            left = max(0.0, total - spent)
            return _clock(left + 0.999), left <= 0
        return _clock(spent), False

    def segments() -> list[dict]:
        clock, done = shown()
        head = f"{label}  " if label else ""
        if done:
            state["before"], state["since"] = total, None
            return [{"label": f"{head}{clock} · time is up", "value": "done"},
                    {"label": "↺ reset", "value": "reset"}]  # fmt: skip
        verb = "⏸ pause" if state["since"] is not None else "⏵ start"
        return [
            {"label": f"{head}{clock}", "value": "toggle"},
            {"label": verb, "value": "toggle"},
            {"label": "↺ reset", "value": "reset"},
        ]

    if not ui.live:
        clock, _ = shown()
        slot.add(Text(f"{label + ': ' if label else ''}{clock}", tone="accent"))
        return
    drawn = slot.add(Button(segments=segments(), focused_hint="  ←→ · enter"))
    state["drawn"] = drawn.node

    def tick() -> None:
        if state["drawn"] is not drawn.node or state["since"] is None:
            return
        drawn.node.update(segments=segments())
        if state["since"] is not None:
            asyncio.get_running_loop().call_later(1.0, tick)

    def pressed(ev: Any) -> None:
        if ev.kind != "select":
            return
        match ev.value:
            case "toggle" if state["since"] is None:
                state["since"] = time.monotonic()
                asyncio.get_running_loop().call_later(1.0, tick)
            case "toggle":
                state["before"], state["since"] = elapsed(), None
            case "reset":
                state["before"], state["since"] = 0.0, None
        drawn.node.update(segments=segments())

    ui.block.listen(drawn.node, pressed)
    if state["since"] is not None:
        asyncio.get_running_loop().call_later(1.0, tick)


HANDLERS = {
    **{kind: control for kind in CONTROLS},
    "button": button,
    "form": form,
    "timer": timer,
}

__all__ = ["CONTROLS", "HANDLERS"]
