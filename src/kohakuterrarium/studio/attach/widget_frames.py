"""Handle client-sent widget frames: ``client_hello``, ``widget_msg``, ``widget_state``,
``inline_ui_state``.

Shared by the local attach (``io.py``) and the worker-side lab adapter, which
each resolve the addressed creature and pass in a frame-put callable.
"""

import time
from collections.abc import Callable, Iterable
from typing import Any

from kohakuterrarium.modules.output.widgets.service import WidgetMessage
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

WIDGET_CLIENT_FRAMES = frozenset(
    {"client_hello", "widget_msg", "widget_state", "inline_ui_state"}
)


def apply_client_hello(frame: dict, outputs: Iterable[Any]) -> None:
    """Give every stream output of one connection the client's renderer map."""
    renderers = frame.get("renderers")
    for output in outputs:
        setter = getattr(output, "set_client_renderers", None)
        if setter is not None:
            setter(renderers if isinstance(renderers, dict) else {})


def _ack(
    put: Callable[[dict], None], event_id: str, kind: str, status: str, source: str
) -> None:
    try:
        put(
            {
                "type": "widget_ack",
                "event_id": event_id,
                "kind": kind,
                "status": status,
                "source": source,
                "ts": time.time(),
            }
        )
    except Exception:
        logger.debug("widget_ack dropped", exc_info=True)


def handle_widget_msg(
    frame: dict, agent: Any, source_name: str, put: Callable[[dict], None]
) -> None:
    """Route one ``widget_msg`` frame to ``agent``'s widget service and ack it."""
    event_id = frame.get("event_id")
    name = frame.get("name")
    if not isinstance(event_id, str) or not event_id:
        return
    service = getattr(getattr(agent, "output_router", None), "widgets", None)
    if service is None or not isinstance(name, str):
        _ack(put, event_id, "message", "unknown", source_name)
        return
    data = frame.get("data")
    origin = "app" if frame.get("origin") == "app" else "renderer"
    ts = frame.get("ts")
    msg = WidgetMessage(
        widget_id=event_id,
        name=name,
        data=data if isinstance(data, dict) else {},
        origin=origin,
        user=frame.get("user") if isinstance(frame.get("user"), str) else None,
        ts=float(ts) if isinstance(ts, (int, float)) else time.time(),
    )
    try:
        status = service.submit_message(msg)
    except Exception as e:
        logger.warning("widget message failed", error=str(e), exc_info=True)
        status = "unknown"
    _ack(put, event_id, "message", status, source_name)


def handle_widget_state(
    frame: dict,
    agent: Any,
    source_output: Any,
    source_name: str,
    put: Callable[[dict], None],
) -> None:
    """Merge one ``widget_state`` frame; ack only when it was not accepted."""
    event_id = frame.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return
    service = getattr(getattr(agent, "output_router", None), "widgets", None)
    state = frame.get("state")
    if service is None or not isinstance(state, dict):
        _ack(
            put,
            event_id,
            "state",
            "unknown" if service is None else "dropped",
            source_name,
        )
        return
    try:
        status = service.submit_state(event_id, state, source=source_output)
    except Exception as e:
        logger.warning("widget state failed", error=str(e), exc_info=True)
        status = "unknown"
    if status != "accepted":
        _ack(put, event_id, "state", status, source_name)


def handle_inline_ui_state(
    frame: dict,
    agent: Any,
    source_output: Any,
    source_name: str,
    put: Callable[[dict], None],
) -> None:
    """Merge one ``inline_ui_state`` frame; ack only when it was not accepted."""
    block = frame.get("block")
    turn, branch = frame.get("ui_turn"), frame.get("ui_branch")
    service = getattr(getattr(agent, "output_router", None), "inline_ui", None)
    if service is None:
        status = "unknown"
    else:
        try:
            status = service.submit(
                turn, branch, block, frame.get("state"), source=source_output
            )
        except Exception as e:
            logger.warning("inline ui state failed", error=str(e), exc_info=True)
            status = "unknown"
    if status != "accepted":
        _ack(put, f"{turn}:{branch}:{block}", "inline_ui_state", status, source_name)


def handle_widget_frame(
    msg_type: str,
    frame: dict,
    agent: Any,
    source_output: Any,
    source_name: str,
    put: Callable[[dict], None],
) -> None:
    """Dispatch one non-hello widget frame to its handler."""
    match msg_type:
        case "widget_msg":
            handle_widget_msg(frame, agent, source_name, put)
        case "widget_state":
            handle_widget_state(frame, agent, source_output, source_name, put)
        case "inline_ui_state":
            handle_inline_ui_state(frame, agent, source_output, source_name, put)
