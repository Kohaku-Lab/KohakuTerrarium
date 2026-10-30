"""Explicit removal of missing local images from completed historical turns."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.errors import ConflictError
from kohakuterrarium.core.conversation.continuation import (
    live_branch_path,
    require_no_running_jobs,
)
from kohakuterrarium.llm.media.reference import file_reference_path
from kohakuterrarium.session.history import select_live_event_ids
from kohakuterrarium.session.resume_build import build_conversation
from kohakuterrarium.session.snapshot import save_agent_snapshot, snapshot_tag


@dataclass(frozen=True)
class MissingHistoryImage:
    """One missing image occurrence in an identified historical message."""

    message_index: int
    part_index: int
    turn_index: int
    url: str


def _pair(metadata: dict) -> tuple[int, int] | None:
    turn, branch = metadata.get("turn_index"), metadata.get("branch_id")
    if (
        metadata.get("origin_known") is not False
        and type(turn) is int
        and turn > 0
        and type(branch) is int
        and branch > 0
    ):
        return turn, branch
    return None


def missing_history_images(
    messages: list[dict], events: list[dict], branch: dict[int, int], current_turn: int
) -> list[MissingHistoryImage]:
    """Scan absent files in completed older live turns (requires blocking I/O)."""
    live_ids = select_live_event_ids(events, branch_view=branch)
    ended, rejected = set(), set()
    origins: dict[str, set[tuple[int, int]]] = {}
    for event in events:
        if event.get("event_id") not in live_ids:
            continue
        key = (event.get("turn_index"), event.get("branch_id"))
        if event.get("type") == "tool_call" and _pair(event):
            job_id = event.get("call_id") or event.get("job_id")
            if job_id:
                origins.setdefault(str(job_id), set()).add(key)
        if event.get("type") == "processing_start":
            ended.discard(key)
        if event.get("type") == "processing_end":
            ended.add(key)
        if event.get("type") in {"processing_error", "activity:interrupt"}:
            rejected.add(key)
    eligible = ended - rejected
    found = []
    open_pair = None
    for index, message in enumerate(messages):
        metadata = message.get("metadata") or {}
        pair = _pair(metadata)
        feedback = metadata.get("kind") == "tool_results"
        if message.get("role") == "user" and not feedback:
            open_pair = pair
        if message.get("role") == "tool" or feedback:
            identities = (
                metadata.get("job_ids") if feedback else [metadata.get("job_id")]
            )
            pair = pair or open_pair
            if (
                metadata.get("origin_known") is False
                or metadata.get("tool_result_phase") == "pending"
                or not identities
                or any(origins.get(str(identity)) != {pair} for identity in identities)
            ):
                pair = None
        if (
            message.get("role") not in {"user", "tool"}
            or pair is None
            or not 0 < pair[0] < current_turn
            or pair not in eligible
            or not isinstance(message.get("content"), list)
        ):
            continue
        for part_index, part in enumerate(message["content"]):
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            path = file_reference_path(url)
            if path is None or not path.is_absolute():
                continue
            try:
                path.stat()
            except FileNotFoundError:
                found.append(MissingHistoryImage(index, part_index, pair[0], url))
            except OSError:
                continue
    return found


async def repair_history_images(
    agent: Any, confirmation: str | None = None
) -> tuple[list[MissingHistoryImage], str]:
    """Preview or confirm a branch-bound repair, persisting before live adoption."""
    async with agent._get_message_mutation_lock():
        await agent._wait_for_history_finalization()
        agent._ensure_history_mutation_idle()
        require_no_running_jobs(agent)
        store = agent.session_store
        if store is None:
            raise ValueError("Image repair requires a persisted session.")
        output = getattr(agent, "_session_output", None)
        if output is not None:
            await output.drain()
        agent._ensure_history_mutation_idle()
        require_no_running_jobs(agent)
        conversation = agent.controller.conversation
        messages = conversation.snapshot_messages(preserve_pending_tail=True)
        events = store.get_events(agent.config.name)
        branch = live_branch_path(agent)
        found = missing_history_images(messages, events, branch, agent._turn_index)
        encoded = json.dumps(
            [
                str(store.path),
                agent.config.name,
                snapshot_tag(agent),
                messages,
                [vars(item) for item in found],
            ],
            sort_keys=True,
            ensure_ascii=True,
        ).encode()
        token = hashlib.sha256(encoded).hexdigest()[:24]
        if confirmation is None:
            return found, token
        if confirmation != token:
            raise ConflictError(
                "History or missing images changed. Run /repair-images again."
            )
        if not found:
            return found, token
        repaired = deepcopy(messages)
        for item in found:
            repaired[item.message_index]["content"][item.part_index] = {
                "type": "text",
                "text": (
                    f"[Historical image unavailable: {item.url}. The local file is "
                    "missing. This request does not include the image. Ask the user "
                    "to provide it again if its contents are needed.]"
                ),
            }
        replacement = build_conversation(repaired)
        save_agent_snapshot(store, agent.config.name, agent, repaired)
        store.flush()
        conversation.adopt_contents(replacement)
        return found, token
