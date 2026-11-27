"""Explicit removal of missing local images from completed historical turns."""

import asyncio
import hashlib
import json
import os
import stat
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kohakuterrarium.errors import ConflictError, InvalidRequestError, NotFoundError
from kohakuterrarium.core.conversation.continuation import (
    live_branch_path,
    require_no_running_jobs,
)
from kohakuterrarium.llm.media.reference import file_reference_path, session_media_path
from kohakuterrarium.session.history.replay import select_live_event_ids
from kohakuterrarium.session.resume.build import build_conversation
from kohakuterrarium.session.store.records.snapshot import (
    save_agent_snapshot,
    snapshot_tag,
)

# The event fields branch selection and turn eligibility read.
_SELECTION_FIELDS = frozenset(
    {
        "event_id",
        "type",
        "turn_index",
        "branch_id",
        "parent_branch_path",
        "call_id",
        "job_id",
        "origin_known",
    }
)


@dataclass(frozen=True)
class MissingHistoryImage:
    """One missing image occurrence in an identified historical message."""

    message_index: int
    part_index: int
    turn_index: int | None
    url: str
    kind: str = "file"
    repairable: bool = True


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


def inspect_history_images(
    messages: list[dict],
    events: list[dict],
    branch: dict[int, int],
    current_turn: int,
    *,
    session_path: str | Path | None = None,
    include_completed_tip: bool = False,
) -> list[MissingHistoryImage]:
    """Report unavailable local references and whether each can be replaced."""
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
    availability: dict[str, tuple[str, bool] | None] = {}
    open_pair = None
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
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
                or not isinstance(identities, (list, tuple))
                or not identities
                or any(origins.get(str(identity)) != {pair} for identity in identities)
            ):
                pair = None
        can_replace = (
            message.get("role") in {"user", "tool"}
            and pair is not None
            and pair in eligible
            and (
                pair[0] < current_turn
                or include_completed_tip
                and pair[0] == current_turn
            )
        )
        if not isinstance(message.get("content"), list):
            continue
        for part_index, part in enumerate(message["content"]):
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str):
                continue
            if url not in availability:
                availability[url] = _unavailable_image(url, session_path)
            problem = availability[url]
            if problem is not None:
                kind, missing = problem
                found.append(
                    MissingHistoryImage(
                        index,
                        part_index,
                        pair[0] if pair else None,
                        url,
                        kind,
                        can_replace and missing,
                    )
                )
    return found


def _unavailable_image(
    url: str, session_path: str | Path | None
) -> tuple[str, bool] | None:
    if url.startswith("file://"):
        path = file_reference_path(url)
        if path is None or not path.is_absolute():
            return "file", False
        try:
            return None if _readable_image_path(path) else ("file", False)
        except FileNotFoundError:
            return "file", True
        except (OSError, ValueError):
            return "file", False
    if session_path is not None and url.startswith("/api/sessions/"):
        try:
            path = session_media_path(url, session_path=Path(session_path))
            return None if path and _readable_image_path(path) else ("artifact", False)
        except (NotFoundError, FileNotFoundError):
            return "artifact", True
        except (OSError, ValueError, InvalidRequestError):
            return "artifact", False
    return None


def _readable_image_path(path: Path) -> bool:
    return stat.S_ISREG(path.stat().st_mode) and os.access(path, os.R_OK)


def missing_history_images(
    messages: list[dict],
    events: list[dict],
    branch: dict[int, int],
    current_turn: int,
    *,
    session_path: str | Path | None = None,
) -> list[MissingHistoryImage]:
    """Find repairable occurrences in completed older turns."""
    return [
        item
        for item in inspect_history_images(
            messages, events, branch, current_turn, session_path=session_path
        )
        if item.repairable
    ]


def replace_missing_images(
    messages: list[dict], found: list[MissingHistoryImage]
) -> list[dict]:
    """Replace approved, repairable image parts on a detached conversation."""
    repaired = deepcopy(messages)
    for item in found:
        if not item.repairable:
            continue
        repaired[item.message_index]["content"][item.part_index] = {
            "type": "text",
            "text": (
                f"[Historical image unavailable: {item.url}. The local file is "
                "missing. This request does not include the image. Ask the user "
                "to provide it again if its contents are needed.]"
            ),
        }
    return repaired


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
        events = await store.run(
            store.get_event_fields, agent.config.name, _SELECTION_FIELDS
        )
        agent._ensure_history_mutation_idle()
        require_no_running_jobs(agent)
        conversation = agent.controller.conversation
        messages = conversation.snapshot_messages(preserve_pending_tail=True)
        branch = live_branch_path(agent)
        turn = agent._turn_index
        found = await asyncio.to_thread(
            missing_history_images,
            messages,
            events,
            branch,
            turn,
            session_path=store.path,
        )
        agent._ensure_history_mutation_idle()
        require_no_running_jobs(agent)
        if (
            agent.controller.conversation is not conversation
            or agent._turn_index != turn
            or live_branch_path(agent) != branch
            or conversation.snapshot_messages(preserve_pending_tail=True) != messages
        ):
            raise ConflictError(
                "History changed during the scan. Run /repair-images again."
            )
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
        repaired = replace_missing_images(messages, found)
        replacement = build_conversation(repaired)
        save_agent_snapshot(
            store, agent.config.name, agent, repaired, preserve_other_paths=True
        )
        store.flush()
        conversation.adopt_contents(replacement)
        return found, token
