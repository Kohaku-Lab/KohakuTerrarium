"""Terminal text profile of ``kt.ui``: a widget drawn as cards, selections and prompts.

Terminal outputs (Rich CLI, TUI) cannot draw the web components, so a
:class:`TerminalUiWidget` turns one ``kt.ui`` widget into the interactive
event kinds they already render: a ``card`` showing the text form with one
action per control, a ``selection`` for choosing an option, an ``ask_text``
for typing a value. Their replies come back through the widget acting as a
reply router, and become widget state and widget messages.
"""

import secrets
from dataclasses import dataclass
from typing import Any, Callable

from kohakuterrarium.builtins.kt_ui.expr import (
    ExprError,
    evaluate,
    interpolate,
    to_display,
    truthy,
)
from kohakuterrarium.builtins.kt_ui.fallback import render_fallback
from kohakuterrarium.builtins.kt_ui.spec import (
    SpecError,
    ValidatedSpec,
    build_scope,
    validate_spec,
)
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.modules.output.widgets import WidgetMessage
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

RENDERER = "kt.ui"
TEXT_PROFILE = {RENDERER: 1}
MAX_CONTROLS = 24
_CHOICE_TYPES = frozenset({"select", "radio"})
_FLIP_TYPES = frozenset({"toggle", "checkbox"})
_TYPED_TYPES = frozenset({"number", "slider", "text_input", "date"})

Present = Callable[[OutputEvent, Any], None]


@dataclass
class Control:
    """One interactive node, with the scope it was found in."""

    control_id: str
    node: dict[str, Any]
    scope: dict[str, Any]
    label: str


def _text(value: Any, scope: dict[str, Any]) -> str:
    if not isinstance(value, str):
        return to_display(value)
    try:
        return to_display(interpolate(value, scope))
    except ExprError:
        return "?"


def _resolve_deep(value: Any, scope: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return interpolate(value, scope)
        except ExprError:
            return None
    if isinstance(value, dict):
        return {k: _resolve_deep(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_deep(v, scope) for v in value]
    return value


def _visible(node: dict, scope: dict[str, Any]) -> bool:
    if "visible" not in node:
        return True
    value = node["visible"]
    if isinstance(value, str):
        try:
            value = interpolate(value, scope)
        except ExprError:
            return False
    return truthy(value)


def _bound_names(node: Any) -> list[str]:
    """``bind`` targets inside ``node``, in tree order."""
    names: list[str] = []
    if not isinstance(node, dict):
        return names
    if isinstance(node.get("bind"), str):
        names.append(node["bind"])
    for child in node.get("children") or []:
        names.extend(_bound_names(child))
    if isinstance(node.get("child"), dict):
        names.extend(_bound_names(node["child"]))
    return names


def _options(node: dict, scope: dict[str, Any]) -> list[tuple[Any, str]]:
    raw = _resolve_deep(node.get("options"), scope)
    out: list[tuple[Any, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            value = item.get("value")
            out.append((value, _text(item.get("label", value), scope)))
        else:
            out.append((item, to_display(item)))
    return out


class TerminalUiWidget:
    """One ``kt.ui`` widget as terminal interactive events.

    ``present(event, reply_router)`` is the output's hook that draws a
    legacy-kind event and sends its reply to ``reply_router``; this object
    is that reply router.
    """

    def __init__(
        self,
        event: OutputEvent,
        *,
        service: Any,
        present: Present,
        retract: Callable[[str], None],
        source: Any = None,
    ) -> None:
        payload = event.payload or {}
        self.widget_id = event.id or ""
        self.title = str(payload.get("title") or "")
        self.interactive = bool(event.interactive)
        self.closed = False
        self.state: dict[str, Any] = dict(payload.get("state") or {})
        self._validated = self._validate(payload.get("spec"))
        self._service = service
        self._present = present
        self._retract = retract
        self._source = source
        self._controls: dict[str, Control] = {}
        self._pending: dict[str, Control] = {}
        self._card_id: str | None = None
        self._menu_open = True

    @staticmethod
    def _validate(spec: Any) -> ValidatedSpec | None:
        try:
            return validate_spec(spec)
        except SpecError as e:
            logger.debug("kt.ui spec not drawable in the terminal", error=str(e))
            return None

    # -- drawing ---------------------------------------------------------

    def scope(self) -> dict[str, Any]:
        if self._validated is None:
            return dict(self.state)
        return build_scope(self._validated, self.state)

    def text(self) -> str:
        if self._validated is None:
            return self.title or "(widget)"
        return render_fallback(self._validated, self.state)

    def controls(self) -> list[Control]:
        """Interactive nodes in tree order (at most ``MAX_CONTROLS``)."""
        found: list[Control] = []
        if self._validated is not None and not self.closed:
            self._collect(self._validated.spec.get("root"), self.scope(), found)
        return found[:MAX_CONTROLS]

    def _collect(self, node: Any, scope: dict[str, Any], out: list[Control]) -> None:
        if not isinstance(node, dict) or not _visible(node, scope):
            return
        node_type = node.get("type")
        if node_type == "each":
            try:
                items = evaluate(node.get("items", "[]"), scope)
            except ExprError:
                return
            name = node.get("as", "item")
            for index, item in enumerate(items if isinstance(items, list) else []):
                child_scope = {**scope, name: item, "index": index}
                self._collect(node.get("child"), child_scope, out)
            return
        if node_type in _CHOICE_TYPES | _FLIP_TYPES | _TYPED_TYPES:
            if not self.interactive and not isinstance(node.get("bind"), str):
                return
            label = _text(node.get("label", node.get("bind")), scope)
            current = to_display(scope.get(node.get("bind")))
            self._add(out, node, scope, f"{label}: {current} ✎")
        elif node_type == "button" and (node.get("set") or node.get("send")):
            disabled = node.get("disabled")
            if disabled is not None and truthy(_resolve_deep(disabled, scope)):
                return
            if node.get("send") and not self.interactive:
                return
            self._add(out, node, scope, _text(node.get("label"), scope))
        for child in node.get("children") or []:
            self._collect(child, scope, out)
        if node_type == "form" and self.interactive:
            self._add(out, node, scope, _text(node.get("submit", "Submit"), scope))

    @staticmethod
    def _add(out: list[Control], node: dict, scope: dict, label: str) -> None:
        out.append(Control(f"c{len(out)}", node, scope, label))

    def draw(self) -> None:
        """Present the widget as a card, retracting the previous one."""
        if self._card_id is not None:
            self._retract(self._card_id)
        controls = self.controls() if self._menu_open else []
        self._controls = {c.control_id: c for c in controls}
        self._card_id = f"{self.widget_id}#{secrets.token_hex(3)}"
        actions = [{"id": c.control_id, "label": c.label} for c in controls]
        payload: dict[str, Any] = {
            "title": self.title or "Widget",
            "body": self.text(),
            "actions": actions,
        }
        if self.closed:
            payload["footer"] = "closed"
        card = OutputEvent(
            type="card",
            id=self._card_id,
            payload=payload,
            interactive=bool(actions),
        )
        self._present(card, self)

    # -- backend events ----------------------------------------------------

    def apply_update(self, patch: dict[str, Any]) -> None:
        """Apply a widget update (spec replace, state merge, title) and redraw.

        An update that only flags a waiting tool redraws just to reopen a
        dismissed menu.
        """
        if not {"spec", "state", "title"} & patch.keys():
            if patch.get("awaiting") is True and not self._menu_open:
                self._menu_open = True
                self.draw()
            return
        if "spec" in patch:
            validated = self._validate(patch["spec"])
            if validated is not None:
                self._validated = validated
        if isinstance(patch.get("state"), dict):
            self.state.update(patch["state"])
        if patch.get("title"):
            self.title = str(patch["title"])
        self._menu_open = True
        self.draw()

    def apply_state(self, state: dict[str, Any]) -> None:
        """Take the merged state another client saved, and redraw."""
        self.state = dict(state)
        self.draw()

    def close(self) -> None:
        self.closed = True
        for event_id in list(self._pending):
            self._retract(event_id)
        self._pending.clear()
        self.draw()

    # -- reply router --------------------------------------------------------

    def submit_reply_with_status(self, reply: UIReply) -> tuple[bool, str]:
        """Take a reply to one of this widget's cards or prompts."""
        if self.closed:
            return False, "unknown"
        prompt = self._pending.pop(reply.event_id, None)
        if prompt is not None:
            self._finish_prompt(prompt, reply)
            return True, "accepted"
        if reply.event_id != self._card_id:
            return False, "unknown"
        control = self._controls.get(reply.action_id)
        if control is None:
            self._menu_open = False
            self._card_id = None
            return True, "accepted"
        self._card_id = None
        self._activate(control)
        return True, "accepted"

    def submit_reply(self, reply: UIReply) -> bool:
        return self.submit_reply_with_status(reply)[0]

    def _activate(self, control: Control) -> None:
        node, scope = control.node, control.scope
        match node.get("type"):
            case "button":
                self._press(node, scope)
                self.draw()
            case "form":
                values = {name: scope.get(name) for name in _bound_names(node)}
                extra = _resolve_deep(node.get("data") or {}, scope)
                if isinstance(extra, dict):
                    values.update(extra)
                self._send(str(node.get("send")), values)
                self.draw()
            case t if t in _FLIP_TYPES:
                self._set({node["bind"]: not (scope.get(node["bind"]) is True)})
                self.draw()
            case t if t in _CHOICE_TYPES:
                self._prompt_choice(control)
            case _:
                self._prompt_text(control)

    def _press(self, node: dict, scope: dict[str, Any]) -> None:
        updates: dict[str, Any] = {}
        for key, expr in (node.get("set") or {}).items():
            try:
                updates[key] = evaluate(expr, scope)
            except ExprError:
                continue
        if updates:
            self._set(updates)
        if isinstance(node.get("send"), str):
            data = _resolve_deep(node.get("data") or {}, scope)
            self._send(node["send"], data if isinstance(data, dict) else {})

    def _prompt_choice(self, control: Control) -> None:
        options = _options(control.node, control.scope)
        prompt_id = f"{self.widget_id}#{secrets.token_hex(3)}"
        self._pending[prompt_id] = control
        current = control.scope.get(control.node.get("bind"))
        default = next(
            (str(i) for i, (value, _) in enumerate(options) if value == current), None
        )
        event = OutputEvent(
            type="selection",
            id=prompt_id,
            interactive=True,
            payload={
                "prompt": control.label.removesuffix(" ✎"),
                "options": [
                    {"id": str(i), "label": label}
                    for i, (_, label) in enumerate(options)
                ],
                "default": default,
            },
        )
        self._present(event, self)

    def _prompt_text(self, control: Control) -> None:
        prompt_id = f"{self.widget_id}#{secrets.token_hex(3)}"
        self._pending[prompt_id] = control
        current = control.scope.get(control.node.get("bind"))
        event = OutputEvent(
            type="ask_text",
            id=prompt_id,
            interactive=True,
            payload={
                "prompt": control.label.removesuffix(" ✎"),
                "default": "" if current is None else to_display(current),
                "multiline": control.node.get("multiline") is True,
            },
        )
        self._present(event, self)

    def _finish_prompt(self, control: Control, reply: UIReply) -> None:
        node = control.node
        bind = node.get("bind")
        if reply.action_id != "submit" or not isinstance(bind, str):
            self.draw()
            return
        values = reply.values or {}
        if node.get("type") in _CHOICE_TYPES:
            options = _options(node, control.scope)
            selected = values.get("selected")
            if isinstance(selected, list):
                selected = selected[0] if selected else None
            try:
                self._set({bind: options[int(str(selected))][0]})
            except (ValueError, IndexError):
                pass
        else:
            parsed = self._parse(node, str(values.get("text", "")))
            if parsed is not None:
                self._set({bind: parsed})
        self.draw()

    @staticmethod
    def _parse(node: dict, text: str) -> Any:
        text = text.strip()
        if node.get("type") not in ("number", "slider"):
            return text
        try:
            value = float(text)
        except ValueError:
            return None
        return int(value) if value.is_integer() and "." not in text else value

    def _set(self, updates: dict[str, Any]) -> None:
        self.state.update(updates)
        try:
            self._service.submit_state(
                self.widget_id, dict(updates), source=self._source
            )
        except RuntimeError as e:
            logger.warning("kt.ui state not saved", error=str(e))

    def _send(self, name: str, data: dict[str, Any]) -> None:
        msg = WidgetMessage(self.widget_id, name, data, origin="renderer")
        try:
            status = self._service.submit_message(msg)
        except RuntimeError as e:
            logger.warning("kt.ui message not sent", error=str(e))
            return
        logger.debug("kt.ui message", widget_id=self.widget_id, status=status)


class TerminalUiRenderer:
    """The ``kt.ui`` widgets one terminal output is showing."""

    def __init__(
        self,
        *,
        get_service: Callable[[], Any],
        present: Present,
        retract: Callable[[str], None],
        source: Any = None,
    ) -> None:
        self._get_service = get_service
        self._present = present
        self._retract = retract
        self._source = source
        self.widgets: dict[str, TerminalUiWidget] = {}

    def handles(self, event: OutputEvent) -> bool:
        """Whether ``event`` opens or targets a ``kt.ui`` widget drawn here."""
        target = event.update_target or event.id
        if event.type == "widget" and event.update_target is None:
            return (event.payload or {}).get("renderer") == RENDERER
        return target in self.widgets

    def on_widget(self, event: OutputEvent) -> None:
        target = event.update_target or event.id or ""
        payload = event.payload or {}
        match event.type:
            case "widget" if event.update_target is None:
                service = self._get_service()
                if service is None:
                    return
                widget = TerminalUiWidget(
                    event,
                    service=service,
                    present=self._present,
                    retract=self._retract,
                    source=self._source,
                )
                self.widgets[widget.widget_id] = widget
                widget.draw()
            case "widget":
                self.widgets[target].apply_update(payload)
            case "widget_state" if isinstance(payload.get("state"), dict):
                self.widgets[target].apply_state(payload["state"])
            case "widget_close":
                self.widgets.pop(target).close()


__all__ = [
    "RENDERER",
    "TEXT_PROFILE",
    "Control",
    "TerminalUiRenderer",
    "TerminalUiWidget",
]
