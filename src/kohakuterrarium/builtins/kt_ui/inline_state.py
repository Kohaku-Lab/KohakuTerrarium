"""Interaction state of inline ``<ui>`` blocks in assistant replies.

A block is addressed by the turn and branch of its reply plus a block id: the
FNV-1a hash of its source and its occurrence among identical blocks
(``"1a2b3c4d.0"``). The service checks the block exists in the recorded reply,
keeps only keys the block declares in ``state`` plus UI-only state under
``__ui`` (node path -> object: table sort, open tree nodes …), stores the merged state and
broadcasts it as an ``inline_ui_state`` event (recorded by the session output,
forwarded to attached clients).
"""

import asyncio
import json
import re
from typing import Any

from kohakuterrarium.builtins.kt_ui.markup import (
    MarkupError,
    convert_block,
    split_message,
)
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

INLINE_UI_STATE = "inline_ui_state"
MAX_STATE_BYTES = 64 * 1024
MAX_CACHED_BLOCKS = 512
BLOCK_ID = re.compile(r"[0-9a-f]{8}\.\d{1,4}")
UI_STATE_KEY = "__ui"
MAX_UI_ENTRIES = 256
UI_PATH = re.compile(r"[A-Za-z0-9_.\[\]:-]{1,200}")

_FNV_OFFSET = 0x811C9DC5
_FNV_PRIME = 0x01000193


def block_hash(source: str) -> str:
    """FNV-1a (32-bit) of the UTF-8 source, as 8 hex digits; matches the web client."""
    value = _FNV_OFFSET
    for byte in source.encode("utf-8"):
        value = ((value ^ byte) * _FNV_PRIME) & 0xFFFFFFFF
    return f"{value:08x}"


def block_ids(text: str) -> list[tuple[str, str]]:
    """``(block id, source)`` for every closed ``<ui>`` block in one text."""
    seen: dict[str, int] = {}
    found: list[tuple[str, str]] = []
    for segment in split_message(text):
        if segment["kind"] != "ui" or not segment.get("closed"):
            continue
        digest = block_hash(segment["source"])
        index = seen.get(digest, 0)
        seen[digest] = index + 1
        found.append((f"{digest}.{index}", segment["source"]))
    return found


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict):
                kind, text = part.get("type"), part.get("text")
            else:
                kind, text = getattr(part, "type", None), getattr(part, "text", None)
            if kind == "text" and isinstance(text, str):
                texts.append(text)
        return "".join(texts)
    return ""


def _ui_patch(value: Any) -> dict[str, dict[str, Any]]:
    """The valid entries of a client's UI-only state: node path -> object."""
    if not isinstance(value, dict):
        return {}
    return {
        path: entry
        for path, entry in value.items()
        if isinstance(path, str) and UI_PATH.fullmatch(path) and isinstance(entry, dict)
    }


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


class InlineUiStates:
    """Validate, store and broadcast inline ``<ui>`` block state for one agent."""

    def __init__(self, router: Any) -> None:
        self._router = router
        self.agent: Any = None
        self._states: dict[tuple[int, int, str], dict[str, Any]] = {}
        self._keys: dict[tuple[int, int, str], frozenset[str]] = {}
        self._restored_from: int | None = None
        self._tasks: set[asyncio.Task] = set()

    def bind_agent(self, agent: Any) -> None:
        self.agent = agent

    def get(self, turn: int, branch: int, block: str) -> dict[str, Any] | None:
        """The stored state of one block, restoring from the session when needed."""
        self._restore_once()
        state = self._states.get((turn, branch, block))
        return dict(state) if state is not None else None

    def submit(
        self,
        turn: Any,
        branch: Any,
        block: Any,
        state: Any,
        *,
        source: Any = None,
    ) -> str:
        """Merge client state into one block: ``accepted``, ``unknown`` or ``dropped``."""
        if not (
            isinstance(turn, int)
            and isinstance(branch, int)
            and isinstance(block, str)
            and BLOCK_ID.fullmatch(block)
        ):
            return "unknown"
        if not isinstance(state, dict):
            return "dropped"
        key = (turn, branch, block)
        allowed = self._allowed_keys(key)
        if allowed is None:
            return "unknown"
        patch = {k: v for k, v in state.items() if k in allowed}
        ui_patch = _ui_patch(state.get(UI_STATE_KEY))
        if not patch and not ui_patch:
            return "dropped"
        self._restore_once()
        current = self._states.get(key, {})
        merged = {**current, **patch}
        if ui_patch:
            ui = {**(current.get(UI_STATE_KEY) or {}), **ui_patch}
            if len(ui) > MAX_UI_ENTRIES:
                return "dropped"
            merged[UI_STATE_KEY] = ui
        if _json_size(merged) > MAX_STATE_BYTES:
            return "dropped"
        self._states[key] = merged
        self._schedule(
            self._router.emit_widget(
                OutputEvent(
                    type=INLINE_UI_STATE,
                    id=f"{turn}:{branch}:{block}",
                    payload={
                        "ui_turn": turn,
                        "ui_branch": branch,
                        "block": block,
                        "state": dict(merged),
                    },
                ),
                exclude=source,
            )
        )
        return "accepted"

    def _allowed_keys(self, key: tuple[int, int, str]) -> frozenset[str] | None:
        if key in self._keys:
            return self._keys[key]
        turn, branch, block = key
        for text in self._reply_texts(turn, branch):
            for found, source in block_ids(text):
                if found != block:
                    continue
                try:
                    spec = convert_block(source)
                except MarkupError:
                    return None
                names = frozenset((spec.get("state") or {}).keys())
                if len(self._keys) >= MAX_CACHED_BLOCKS:
                    self._keys.pop(next(iter(self._keys)))
                self._keys[key] = names
                return names
        return None

    def _reply_texts(self, turn: int, branch: int) -> list[str]:
        """Assistant texts of one turn and branch, or of the live conversation without a store."""
        agent = self.agent
        store = getattr(agent, "session_store", None) if agent else None
        if store is None:
            controller = getattr(agent, "controller", None)
            conversation = getattr(controller, "conversation", None)
            if conversation is None:
                return []
            return [
                _text_of(getattr(m, "content", ""))
                for m in conversation.get_messages()
                if getattr(m, "role", "") == "assistant"
            ]
        try:
            events = store.get_events(agent.config.name)
        except Exception as e:
            logger.warning("inline ui lookup failed", error=str(e), exc_info=True)
            return []
        return [
            _text_of((evt.get("message") or {}).get("content"))
            for evt in events
            if evt.get("type") == "assistant_message"
            and evt.get("turn_index") == turn
            and evt.get("branch_id") == branch
        ]

    def _restore_once(self) -> None:
        """Load recorded block state from the bound agent's session, once per store."""
        agent = self.agent
        store = getattr(agent, "session_store", None) if agent else None
        if store is None or self._restored_from == id(store):
            return
        self._restored_from = id(store)
        try:
            events = store.get_events(agent.config.name)
        except Exception as e:
            logger.warning("inline ui restore failed", error=str(e), exc_info=True)
            return
        recorded: dict[tuple[int, int, str], dict[str, Any]] = {}
        for evt in events:
            if evt.get("type") != INLINE_UI_STATE:
                continue
            turn, branch = evt.get("ui_turn"), evt.get("ui_branch")
            block, state = evt.get("block"), evt.get("state")
            if (
                isinstance(turn, int)
                and isinstance(branch, int)
                and isinstance(block, str)
                and isinstance(state, dict)
            ):
                recorded[(turn, branch, block)] = dict(state)
        for key, state in recorded.items():
            self._states.setdefault(key, state)

    def _schedule(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


__all__ = [
    "INLINE_UI_STATE",
    "MAX_STATE_BYTES",
    "UI_STATE_KEY",
    "InlineUiStates",
    "block_hash",
    "block_ids",
]
