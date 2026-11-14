"""Lease-bound semantic actions for one creature's visible companion."""

import asyncio
import inspect
import json
import math
import time
import uuid

from kohakuterrarium.modules.tool.base import BaseTool, ToolResult

from .actions import ACTION_SETS, validate_action_set


def is_live2d_tool(tool):
    """Recognize the package class across file and package import namespaces."""
    return isinstance(tool, Live2DControl) or (
        type(tool).__name__ == "Live2DControl"
        and inspect.getfile(type(tool)) == inspect.getfile(Live2DControl)
    )


class Live2DControl(BaseTool):
    """Dispatch finite gestures while an attached UI grants a live lease."""

    request_replay = "forbid"

    def __init__(self, config=None, purpose="expression"):
        super().__init__(config)
        if purpose not in {"expression", "companion"}:
            raise ValueError("Live2D purpose must be expression or companion.")
        self.purpose = purpose
        self.lease = ""
        self.until = 0.0
        self.model = "dragon"
        self.catalog = ACTION_SETS[self.model]
        self.waiting = False
        self.events = asyncio.Queue(maxsize=1)
        self.last_action = -math.inf

    @property
    def tool_name(self):
        return "l2d"

    @property
    def description(self):
        return "Express with a visible Live2D companion. Use actions to discover gestures, perform to play, release to restore automatic behavior. Read live2d-expression first."

    def get_parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["actions", "perform", "release"],
                },
                "action": {
                    "type": "string",
                    "description": "An ID returned by actions.",
                },
                "seconds": {"type": "number", "minimum": 1, "maximum": 15},
            },
            "required": ["operation"],
            "additionalProperties": False,
        }

    def bind(self, lease, model, waiting=False, action_set=None):
        catalog = validate_action_set(
            action_set if action_set is not None else ACTION_SETS[model], model
        )
        if lease != self.lease or model != self.model or catalog != self.catalog:
            self.revoke()
            self.events = asyncio.Queue(maxsize=1)
            self.last_action = -math.inf
        self.catalog = catalog
        self.lease, self.model, self.waiting = lease, model, waiting
        self.until = time.monotonic() + 20

    def revoke(self):
        self.until = 0
        self.lease = ""
        self.emit({"operation": "release", "id": str(uuid.uuid4())})

    def emit(self, event):
        if self.events.full():
            self.events.get_nowait()
        self.events.put_nowait(event)

    async def execute(self, args, context=None):
        if not self.lease or time.monotonic() >= self.until:
            return ToolResult(
                error="Live2D control is disabled or the companion is unavailable."
            )
        operation = args.get("operation")
        actions = self.catalog["actions"]
        if operation == "actions":
            return ToolResult(
                output=json.dumps(
                    {
                        "model": self.model,
                        "label": self.catalog["label"],
                        "actions": {
                            name: {
                                key: value
                                for key, value in action.items()
                                if key != "playback"
                            }
                            for name, action in actions.items()
                        },
                    }
                )
            )
        if not isinstance(operation, str) or operation not in {"perform", "release"}:
            return ToolResult(error="Unknown operation.")
        command = {"operation": operation, "id": str(uuid.uuid4()), "model": self.model}
        if operation == "perform":
            action_id = args.get("action")
            action = actions.get(action_id) if isinstance(action_id, str) else None
            if action is None:
                return ToolResult(error="Unsupported action; query actions first.")
            seconds = args.get("seconds", action["seconds"])
            # KT's bracket tool format delivers argument values as strings.
            if isinstance(seconds, str):
                try:
                    seconds = float(seconds)
                except ValueError:
                    return ToolResult(error="Duration must be a finite number.")
            if (
                isinstance(seconds, bool)
                or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds)
                or not 1 <= seconds <= action["maxSeconds"]
            ):
                return ToolResult(
                    error="Duration is outside the action's supported range."
                )
            if self.waiting:
                return ToolResult(error="The real waiting-for-input pose has priority.")
            if time.monotonic() - self.last_action < 0.7:
                return ToolResult(
                    error="A gesture was just requested; continue the answer."
                )
            self.last_action = time.monotonic()
            command.update(
                action=args["action"],
                seconds=seconds,
                expiresAt=time.time() * 1000 + seconds * 1000,
            )
        self.emit(command)
        return ToolResult(
            output=json.dumps({"status": "dispatched", "id": command["id"]})
        )
