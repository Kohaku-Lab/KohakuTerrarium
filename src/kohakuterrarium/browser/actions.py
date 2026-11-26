"""The ``actions`` batch of ``browser_act``: parsing, validation and execution.

``parse_actions`` accepts a list of mappings, a JSON string, or a call body
containing JSON, and returns typed :class:`Action` values or raises
``ActionSpecError`` naming the action index and field. ``run_batch`` runs
them in order through an :class:`ActionContext` and stops at the first
failure or dialog.
"""

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from kohakuterrarium.browser.errors import (
    ActionFailedError,
    ActionSpecError,
    DialogOpenedError,
    DriverDisconnectedError,
    DriverTimeoutError,
    NotFileInputError,
    PageClosedError,
)
from kohakuterrarium.browser.protocol import PageDriver
from kohakuterrarium.browser.tasks import discard
from kohakuterrarium.errors import KTError

MAX_ACTIONS = 20
WAIT_MAX_S = 10.0
BATCH_MIN_S = 60.0
BATCH_SLOW_ACTIONS = 3
MAX_TEXT_CHARS = 10000
SLOW_KEY_S = 0.05
SCROLL_DEFAULT_PX = 600
SCROLL_MAX_PX = 100000
BUTTONS = ("left", "right", "middle")
MODIFIERS = ("Alt", "Control", "ControlOrMeta", "Meta", "Shift")
REF_PATTERN = re.compile(r"(?:f\d+)?e\d+")
FIELDS: dict[str, frozenset[str]] = {
    "click": frozenset({"ref", "x", "y", "button", "double", "modifiers"}),
    "type": frozenset({"ref", "text", "secret", "submit", "slowly"}),
    "select": frozenset({"ref", "values"}),
    "check": frozenset({"ref", "checked"}),
    "press": frozenset({"key", "ref"}),
    "hover": frozenset({"ref", "x", "y"}),
    "scroll": frozenset({"ref", "dx", "dy"}),
    "drag": frozenset({"ref", "x", "y", "to", "to_x", "to_y"}),
    "upload": frozenset({"ref", "files"}),
    "wait": frozenset({"text", "text_gone", "seconds"}),
    "dialog": frozenset({"accept", "text"}),
}
KINDS = tuple(FIELDS)
ACTION_NEUTRALS: dict[str, Any] = {
    "ref": "",
    "x": 0,
    "y": 0,
    "button": "left",
    "double": False,
    "modifiers": [],
    "text": "",
    "secret": "",
    "submit": False,
    "slowly": False,
    "values": [],
    "checked": True,
    "key": "",
    "dx": 0,
    "dy": 0,
    "to": "",
    "to_x": 0,
    "to_y": 0,
    "files": [],
    "seconds": 0,
    "text_gone": "",
    "accept": True,
}
ALL_FIELDS = frozenset().union(*FIELDS.values())
_PAIRS = (("x", "y"), ("to_x", "to_y"), ("dx", "dy"))
_OWN_KIND = {"checked": "check", "accept": "dialog"}
_FIRST_OF = {"wait": ("text", "text_gone", "seconds")}
DONE, FAILED, PENDING, SKIPPED = "done", "failed", "dialog pending", "not executed"

_FENCE = re.compile(r"```[A-Za-z]*\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Action:
    """One validated action; fields a kind does not use keep their defaults."""

    index: int
    kind: str
    ref: str | None = None
    x: float | None = None
    y: float | None = None
    button: str = "left"
    double: bool = False
    modifiers: tuple[str, ...] = ()
    text: str | None = field(default=None, repr=False)
    secret: str | None = None
    submit: bool = False
    slowly: bool = False
    values: tuple[str, ...] = ()
    checked: bool = True
    key: str | None = None
    dx: float = 0.0
    dy: float = 0.0
    to: str | None = None
    to_x: float | None = None
    to_y: float | None = None
    files: tuple[int, ...] = ()
    text_gone: str | None = None
    seconds: float | None = None
    accept: bool = True

    @property
    def label(self) -> str:
        """``click e5``, ``press Enter``: what was done, never what was typed."""
        match self.kind:
            case "click" if self.ref is None:
                return f"click at ({self.x:g}, {self.y:g})"
            case "press":
                return f"press {self.key}" + (f" on {self.ref}" if self.ref else "")
            case "hover" if self.ref is None:
                return f"hover at ({self.x:g}, {self.y:g})"
            case "drag":
                start = self.ref or f"({self.x:g}, {self.y:g})"
                end = self.to or f"({self.to_x:g}, {self.to_y:g})"
                return f"drag {start} to {end}"
            case "wait" if self.seconds is not None:
                return f"wait {self.seconds:g} s"
            case "wait":
                return (
                    "wait for text" if self.text_gone is None else "wait for text gone"
                )
            case "dialog":
                return "dialog accept" if self.accept else "dialog dismiss"
            case "scroll" if self.ref is None:
                return f"scroll by ({self.dx:g}, {self.dy:g})"
        return f"{self.kind} {self.ref}" if self.ref else self.kind


@dataclass(frozen=True)
class Outcome:
    """What happened to one action: done, failed, dialog pending or not executed.

    ``reason`` is fixed wording safe outside the untrusted wrapper;
    ``detail`` is page-derived text that goes inside it.
    """

    action: Action
    status: str
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True)
class BatchReport:
    """Every action's outcome; ``lost`` when the browser went away mid-batch."""

    outcomes: list[Outcome]
    lost: bool = False

    @property
    def ok(self) -> bool:
        return all(o.status == DONE for o in self.outcomes)

    @property
    def stopped(self) -> Outcome | None:
        """The action the batch stopped at, if it stopped early."""
        return next((o for o in self.outcomes if o.status in (FAILED, PENDING)), None)


class ActionContext(Protocol):
    """What :func:`run_batch` needs from the session for one tab."""

    page: PageDriver
    timeout: float
    files: Sequence[str]

    def require_no_dialog(self) -> None: ...

    async def type_secret(self, name: str, ref: str) -> None:
        """Type secret ``name`` into ``ref``, judged by that element's own document.

        ``ActionFailedError`` unless that document is on one of the secret's origins.
        """
        ...

    async def check_ref(self, ref: str) -> None:
        """Raise ``ActionFailedError`` unless ``ref`` is known, unchanged and on the page."""
        ...

    async def call(
        self, operation: Callable[[], Awaitable[Any]], timeout: float
    ) -> Any:
        """Run one driver call, bounded, raising ``DialogOpenedError`` if a dialog interrupts it."""
        ...

    async def settle(self) -> None: ...

    async def answer_dialog(self, accept: bool, text: str | None) -> str: ...

    async def pause(self, seconds: float) -> None:
        """Sleep, raising ``DialogOpenedError`` early if a dialog opens meanwhile."""
        ...

    def action_done(self) -> None:
        """Called after every executed action, before settling."""
        ...

    def blocked_landing(self) -> str | None:
        """After an action settled: why its main-frame landing was blocked, else ``None``."""
        ...


def is_neutral(value: Any, neutral: Any) -> bool:
    """Whether ``value`` is the "not given" value ``neutral``, also as text-format text."""
    if value is None:
        return True
    if isinstance(neutral, bool):
        if isinstance(value, str):
            return value.strip().lower() == str(neutral).lower()
        return value is neutral
    if isinstance(neutral, (int, float)):
        if isinstance(value, str):
            try:
                return float(value) == neutral
            except ValueError:
                return False
        return not isinstance(value, bool) and value == neutral
    if isinstance(neutral, list):
        return value in ([], (), "", "[]")
    return value == neutral


def _given(item: dict[str, Any], key: str) -> bool:
    return key in item and not is_neutral(item[key], ACTION_NEUTRALS[key])


def _settle(index: int, kind: str, item: dict[str, Any]) -> None:
    """Apply precedence, then drop neutral values, so a filled-in call means the minimal one.

    A given ``ref`` / ``to`` wins over its coordinates, ``secret`` over
    ``text``, ``ref`` over scroll offsets, and ``wait`` takes the first of
    ``text``, ``text_gone``, ``seconds``. A coordinate pair is dropped only
    when both are 0. A real value on another kind's field is an error.
    """
    for ref, x, y in (("ref", "x", "y"), ("to", "to_x", "to_y")):
        if _given(item, ref):
            item.pop(x, None)
            item.pop(y, None)
    if kind == "scroll" and _given(item, "ref"):
        item.pop("dx", None)
        item.pop("dy", None)
    if kind == "type" and _given(item, "secret"):
        item.pop("text", None)
    order = _FIRST_OF.get(kind, ())
    first = next((key for key in order if _given(item, key)), None)
    for key in order:
        if key != first:
            item.pop(key, None)
    for a, b in _PAIRS:
        if not _given(item, a) and not _given(item, b):
            item.pop(a, None)
            item.pop(b, None)
    for key in [k for k in item if k in ACTION_NEUTRALS]:
        own = _OWN_KIND.get(key)
        if own is not None and own != kind:
            del item[key]
        elif key == "text" and kind == "type":
            continue
        elif not any(key in pair for pair in _PAIRS) and not _given(item, key):
            del item[key]
    for key in sorted(str(k) for k in item):
        if key in ALL_FIELDS and key not in FIELDS[kind]:
            neutral = json.dumps(ACTION_NEUTRALS[key])
            raise _fail(
                index,
                kind,
                f"field {key!r} does not apply to {kind}; set it to {neutral} or "
                "remove it",
            )


def _fail(index: int, kind: str | None, message: str) -> ActionSpecError:
    where = f"actions[{index}]" + (f" ({kind})" if kind else "")
    return ActionSpecError(f"{where}: {message}")


def _load_text(text: str) -> Any:
    body = text.strip()
    fenced = _FENCE.search(body)
    if fenced:
        body = fenced.group(1).strip()
    try:
        return json.loads(body)
    except ValueError:
        pass
    starts = [i for i in (body.find("["), body.find("{")) if i >= 0]
    if not starts:
        raise ActionSpecError(
            "actions must be a JSON list of action objects, e.g. "
            '[{"kind": "click", "ref": "e5"}]'
        )
    try:
        value, _ = json.JSONDecoder().raw_decode(body, min(starts))
    except ValueError as exc:
        raise ActionSpecError(f"actions is not valid JSON: {exc}") from None
    return value


def _items(value: Any) -> list[Any]:
    if isinstance(value, str):
        value = _load_text(value)
    if isinstance(value, Mapping):
        if "kind" not in value and "action" not in value and "actions" in value:
            return _items(value["actions"])
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ActionSpecError("actions must be a list of action objects")
    if not value:
        raise ActionSpecError("actions is empty; give at least one action")
    if len(value) > MAX_ACTIONS:
        raise ActionSpecError(
            f"at most {MAX_ACTIONS} actions per call, got {len(value)}; split the batch"
        )
    return list(value)


def parse_actions(
    value: Any, *, files: Sequence[str] = (), secrets: Sequence[str] | None = None
) -> list[Action]:
    """Validate the ``actions`` argument into typed actions.

    ``files`` is the batch-level file list ``upload`` indexes into;
    ``secrets``, when given, lists the secret names ``type`` may use.
    """
    actions = []
    for index, item in enumerate(_items(value)):
        if not isinstance(item, Mapping):
            raise _fail(index, None, "each action must be an object with a 'kind'")
        actions.append(_parse_one(index, dict(item), len(files), secrets))
    return actions


def _parse_one(
    index: int, item: dict[str, Any], file_count: int, secrets: Sequence[str] | None
) -> Action:
    kind = item.pop("kind", None)
    if kind is None:
        kind = item.pop("action", None)
    if not isinstance(kind, str) or not kind:
        raise _fail(index, None, f"missing 'kind'; one of {', '.join(KINDS)}")
    if kind not in FIELDS:
        raise _fail(index, None, f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    unknown = sorted(str(k) for k in item if k not in ALL_FIELDS)
    if unknown:
        allowed = ", ".join(sorted(FIELDS[kind])) or "none"
        raise _fail(index, kind, f"unknown field {unknown[0]!r}; allowed: {allowed}")
    _settle(index, kind, item)
    reader = _Reader(index, kind, item)
    values: dict[str, Any] = {"index": index, "kind": kind}
    match kind:
        case "click":
            values.update(_click(reader))
        case "type":
            values.update(_type(reader, secrets))
        case "select":
            values["ref"] = reader.ref("ref")
            values["values"] = reader.strings("values")
        case "check":
            values["ref"] = reader.ref("ref")
            values["checked"] = reader.boolean("checked", True)
        case "press":
            values["key"] = reader.text("key", required=True, limit=64)
            values["ref"] = reader.ref("ref", required=False)
        case "hover":
            values.update(reader.point_or_ref("ref", "x", "y"))
        case "scroll":
            values.update(_scroll(reader))
        case "drag":
            values.update(reader.point_or_ref("ref", "x", "y"))
            values.update(reader.point_or_ref("to", "to_x", "to_y"))
        case "upload":
            values["ref"] = reader.ref("ref")
            values["files"] = reader.indexes("files", file_count)
        case "wait":
            values.update(_wait(reader))
        case "dialog":
            values["accept"] = reader.boolean("accept", True)
            values["text"] = reader.text("text", required=False)
    return Action(**values)


def _click(reader: "_Reader") -> dict[str, Any]:
    values: dict[str, Any] = {
        "button": reader.choice("button", BUTTONS, "left"),
        "double": reader.boolean("double", False),
        "modifiers": reader.modifiers(),
    }
    values.update(reader.point_or_ref("ref", "x", "y"))
    return values


def _type(reader: "_Reader", secrets: Sequence[str] | None) -> dict[str, Any]:
    values: dict[str, Any] = {
        "ref": reader.ref("ref"),
        "submit": reader.boolean("submit", False),
        "slowly": reader.boolean("slowly", False),
    }
    if "secret" in reader.item:
        if values["slowly"]:
            raise reader.fail(
                "a secret cannot be typed slowly: it is set in one step on the "
                "checked element; set slowly to false or remove it"
            )
        name = reader.text("secret", required=True, limit=128)
        if secrets is not None and name not in secrets:
            known = ", ".join(secrets) or "none are configured"
            raise reader.fail(f"unknown secret {name!r}; known secrets: {known}")
        values["secret"] = name
    else:
        values["text"] = reader.text("text", required=False, empty=True) or ""
    return values


def _scroll(reader: "_Reader") -> dict[str, Any]:
    if "ref" in reader.item:
        return {"ref": reader.ref("ref")}
    default = 0.0 if "dx" in reader.item else SCROLL_DEFAULT_PX
    dy = reader.number("dy", -SCROLL_MAX_PX, SCROLL_MAX_PX, default)
    return {"dx": reader.number("dx", -SCROLL_MAX_PX, SCROLL_MAX_PX, 0.0), "dy": dy}


def _wait(reader: "_Reader") -> dict[str, Any]:
    given = [k for k in ("text", "text_gone", "seconds") if k in reader.item]
    if not given:
        raise reader.fail(
            "wait needs 'text', 'text_gone' or 'seconds' above 0; set one of them"
        )
    match given[0]:
        case "seconds":
            return {"seconds": reader.number("seconds", 0, WAIT_MAX_S)}
        case "text":
            return {"text": reader.text("text", required=True)}
    return {"text_gone": reader.text("text_gone", required=True)}


class _Reader:
    """Typed field access for one action, raising ``ActionSpecError`` that names the field."""

    def __init__(self, index: int, kind: str, item: dict[str, Any]) -> None:
        self.index, self.kind, self.item = index, kind, item

    def fail(self, message: str) -> ActionSpecError:
        return _fail(self.index, self.kind, message)

    def ref(self, key: str, *, required: bool = True, hint: str = "") -> str | None:
        value = self.item.get(key)
        if value is None:
            if required:
                extra = f" ({hint})" if hint else ""
                raise self.fail(f"field {key!r} is required{extra}")
            return None
        if not isinstance(value, str) or not REF_PATTERN.fullmatch(value.strip()):
            raise self.fail(
                f"field {key!r} must be a ref from a snapshot, like 'e12', got {value!r}"
            )
        return value.strip()

    def point_or_ref(self, ref: str, x: str, y: str) -> dict[str, Any]:
        """The ``ref`` field when given, else the ``x`` / ``y`` pair in CSS pixels."""
        if ref in self.item:
            return {ref: self.ref(ref)}
        if x in self.item or y in self.item:
            return {x: self.number(x, 0, 100000), y: self.number(y, 0, 100000)}
        return {ref: self.ref(ref, hint=f"or '{x}' and '{y}' in CSS pixels")}

    def text(
        self,
        key: str,
        *,
        required: bool,
        empty: bool = False,
        limit: int = MAX_TEXT_CHARS,
    ) -> str | None:
        value = self.item.get(key)
        if value is None:
            if required:
                raise self.fail(f"field {key!r} is required")
            return None
        if not isinstance(value, str):
            raise self.fail(f"field {key!r} must be a string")
        if not value and not empty:
            raise self.fail(f"field {key!r} must not be empty")
        if len(value) > limit:
            raise self.fail(f"field {key!r} is longer than {limit} characters")
        return value

    def boolean(self, key: str, default: bool) -> bool:
        value = self.item.get(key, default)
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        if not isinstance(value, bool):
            raise self.fail(f"field {key!r} must be true or false")
        return value

    def number(
        self, key: str, low: float, high: float, default: float | None = None
    ) -> float:
        value = self.item.get(key, default)
        if value is None:
            raise self.fail(f"field {key!r} is required")
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                raise self.fail(f"field {key!r} must be a number") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self.fail(f"field {key!r} must be a number")
        if not math.isfinite(value) or not low <= value <= high:
            raise self.fail(f"field {key!r} must be between {low:g} and {high:g}")
        return float(value)

    def choice(self, key: str, options: Sequence[str], default: str) -> str:
        value = self.item.get(key, default)
        if value not in options:
            raise self.fail(f"field {key!r} must be one of {', '.join(options)}")
        return value

    def modifiers(self) -> tuple[str, ...]:
        value = self.item.get("modifiers", [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)) or any(
            m not in MODIFIERS for m in value
        ):
            raise self.fail(
                f"field 'modifiers' must list keys from {', '.join(MODIFIERS)}"
            )
        return tuple(value)

    def strings(self, key: str) -> tuple[str, ...]:
        value = self.item.get(key)
        if isinstance(value, str):
            value = [value]
        if (
            not isinstance(value, (list, tuple))
            or not value
            or not all(isinstance(v, str) for v in value)
        ):
            raise self.fail(f"field {key!r} must be a non-empty list of option labels")
        return tuple(value)

    def indexes(self, key: str, file_count: int) -> tuple[int, ...]:
        value = self.item.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            value = [value]
        if not isinstance(value, (list, tuple)) or not value:
            raise self.fail(
                f"field {key!r} must list indexes into the call's 'files' argument"
            )
        for number in value:
            if isinstance(number, bool) or not isinstance(number, int):
                raise self.fail(f"field {key!r} must contain integer indexes")
            if not 0 <= number < file_count:
                raise self.fail(
                    f"file index {number} is out of range: the call's 'files' "
                    f"argument has {file_count} entr{'y' if file_count == 1 else 'ies'}"
                )
        return tuple(value)


def batch_deadline_s(action_timeout: float) -> float:
    """Seconds one batch may run: ``BATCH_MIN_S``, or more for slow action timeouts."""
    return max(BATCH_MIN_S, BATCH_SLOW_ACTIONS * (action_timeout + WAIT_MAX_S))


async def run_batch(
    actions: Sequence[Action], ctx: ActionContext, deadline_s: float | None = None
) -> BatchReport:
    """Run ``actions`` in order; stop at the first failure or interrupting dialog.

    An action whose main-frame navigation the network policy blocked is a
    failure too, and so is the action running when the batch has taken
    ``deadline_s`` (default :func:`batch_deadline_s` of ``ctx.timeout``).
    """
    limit = batch_deadline_s(ctx.timeout) if deadline_s is None else deadline_s
    deadline = asyncio.get_running_loop().time() + limit
    outcomes: list[Outcome] = []
    stopped = False
    lost = False
    for action in actions:
        if stopped:
            outcomes.append(Outcome(action, SKIPPED))
            continue
        try:
            reason = await _execute_by(action, ctx, deadline, limit)
        except DialogOpenedError as exc:
            outcomes.append(Outcome(action, PENDING, str(exc)))
            stopped = True
            continue
        except (DriverDisconnectedError, PageClosedError) as exc:
            lost = isinstance(exc, DriverDisconnectedError)
            reason = "the browser closed unexpectedly" if lost else "the tab was closed"
            outcomes.append(Outcome(action, FAILED, reason))
            stopped = True
            continue
        except KTError as exc:
            detail = _hide_typed(getattr(exc, "detail", ""), action)
            outcomes.append(Outcome(action, FAILED, _reason(exc, action), detail))
            stopped = True
            continue
        ctx.action_done()
        if action.kind != "wait":
            await ctx.settle()
        blocked = ctx.blocked_landing()
        if blocked:
            outcomes.append(Outcome(action, FAILED, blocked))
            stopped = True
            continue
        outcomes.append(Outcome(action, DONE, reason))
    return BatchReport(outcomes, lost)


async def _execute_by(
    action: Action, ctx: ActionContext, deadline: float, limit: float
) -> str:
    """:func:`execute`, failed with ``ActionFailedError`` once the loop time passes ``deadline``."""
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise ActionFailedError(
            f"not started: the batch reached its {limit:g} s time limit"
        )
    task = asyncio.ensure_future(execute(action, ctx))
    try:
        done, _ = await asyncio.wait({task}, timeout=remaining)
    except asyncio.CancelledError:
        discard(task)
        raise
    if task in done:
        return task.result()
    discard(task)
    await asyncio.wait({task})
    raise ActionFailedError(
        f"the batch reached its {limit:g} s time limit while this action ran"
    )


def _reason(exc: BaseException, action: Action) -> str:
    text = str(exc) or type(exc).__name__
    if isinstance(exc, DriverTimeoutError) and "timed out" not in text.lower():
        text = f"timed out: {text}"
    return _hide_typed(text, action)


def _hide_typed(text: str, action: Action) -> str:
    if text and action.text and len(action.text) >= 4:
        return text.replace(action.text, "[typed text]")
    return text


async def execute(action: Action, ctx: ActionContext) -> str:
    """Carry out one action; returns a short detail for the report."""
    if action.kind == "dialog":
        return await ctx.answer_dialog(action.accept, action.text)
    ctx.require_no_dialog()
    for ref in (action.ref, action.to):
        if ref is not None:
            await ctx.check_ref(ref)
    page, t = ctx.page, ctx.timeout
    match action.kind:
        case "click":
            count = 2 if action.double else 1
            options = {
                "button": action.button,
                "click_count": count,
                "modifiers": action.modifiers,
                "timeout": t,
            }
            if action.ref is None:
                await ctx.call(lambda: page.click_at(action.x, action.y, **options), t)
            else:
                await ctx.call(lambda: page.click(action.ref, **options), t)
            return ""
        case "type":
            return await _type_text(action, ctx)
        case "select":
            chosen = await ctx.call(
                lambda: page.select(action.ref, action.values, t), t
            )
            return f"{len(chosen)} option{'' if len(chosen) == 1 else 's'} selected"
        case "check":
            await ctx.call(lambda: page.set_checked(action.ref, action.checked, t), t)
            return "checked" if action.checked else "unchecked"
        case "press":
            await ctx.call(lambda: page.press(action.ref, action.key, t), t)
            return ""
        case "hover" if action.ref is None:
            await ctx.call(lambda: page.hover_at(action.x, action.y, t), t)
            return ""
        case "hover":
            await ctx.call(lambda: page.hover(action.ref, t), t)
            return ""
        case "scroll":
            if action.ref is not None:
                await ctx.call(lambda: page.scroll_into_view(action.ref, t), t)
            else:
                await ctx.call(lambda: page.wheel(action.dx, action.dy, t), t)
            return ""
        case "drag" if action.ref is not None and action.to is not None:
            await ctx.call(lambda: page.drag(action.ref, action.to, t), t)
            return ""
        case "drag":
            start = action.ref or (action.x, action.y)
            end = action.to or (action.to_x, action.to_y)
            await ctx.call(lambda: page.drag_between(start, end, t), t)
            return ""
        case "upload":
            return await _upload(action, ctx)
    return await _wait_action(action, ctx)


async def _type_text(action: Action, ctx: ActionContext) -> str:
    page, t = ctx.page, ctx.timeout
    value = action.text or ""
    if action.secret:
        await ctx.type_secret(action.secret, action.ref)
    elif action.slowly:
        slow = t + len(value) * SLOW_KEY_S
        await ctx.call(lambda: page.fill(action.ref, "", t), t)
        await ctx.call(lambda: page.type_text(action.ref, value, slow), slow)
    else:
        await ctx.call(lambda: page.fill(action.ref, value, t), t)
    if action.submit:
        await ctx.call(lambda: page.press(action.ref, "Enter", t), t)
    what = f"secret {action.secret}" if action.secret else f"{len(value)} characters"
    return f"typed {what}" + (" and pressed Enter" if action.submit else "")


async def _upload(action: Action, ctx: ActionContext) -> str:
    page, t = ctx.page, ctx.timeout
    paths = [ctx.files[i] for i in action.files]
    try:
        await ctx.call(lambda: page.set_input_files(action.ref, paths, t), t)
    except NotFileInputError:
        await ctx.call(lambda: page.choose_files(action.ref, paths, t), t)
        return f"{len(paths)} file{'' if len(paths) == 1 else 's'} chosen via the file dialog"
    return f"{len(paths)} file{'' if len(paths) == 1 else 's'} attached"


async def _wait_action(action: Action, ctx: ActionContext) -> str:
    if action.seconds is not None:
        await ctx.pause(action.seconds)
        return ""
    gone = action.text is None
    text = action.text_gone if gone else action.text
    page, t = ctx.page, ctx.timeout
    try:
        await ctx.call(lambda: page.wait_for_text(text, gone=gone, timeout=t), t)
    except DriverTimeoutError:
        state = "still shown" if gone else "did not appear"
        raise ActionFailedError(f"the text {state} within {t:g} s") from None
    return ""


def format_report(report: BatchReport) -> str:
    """The per-action status lines of a batch."""
    done = sum(1 for o in report.outcomes if o.status == DONE)
    total = len(report.outcomes)
    stop = report.stopped
    head = f"Actions: {done} of {total} done"
    if stop is not None:
        head += f"; stopped at {stop.action.index + 1} ({stop.status})"
    lines = [head]
    for outcome in report.outcomes:
        line = f"{outcome.action.index + 1}. {outcome.action.label}: {outcome.status}"
        if outcome.reason:
            line += f" — {outcome.reason}"
        lines.append(line)
    return "\n".join(lines)


def format_details(
    report: BatchReport, shown: Callable[[str], str] = lambda detail: detail
) -> str:
    """``Action N: <detail>`` lines of page-derived detail, for inside the wrapper.

    ``shown`` turns each detail into the text that is shown.
    """
    return "\n".join(
        f"Action {o.action.index + 1}: {shown(o.detail)}"
        for o in report.outcomes
        if o.detail
    )
