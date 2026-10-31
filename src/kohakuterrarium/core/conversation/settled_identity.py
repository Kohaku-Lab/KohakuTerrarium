"""Resolve snapshot tool-result slots to the persisted jobs they settle."""

import re

from kohakuterrarium.core.conversation.elide import ELISION_MARKER, TOOL_FEEDBACK_KIND
from kohakuterrarium.core.tools.output import render_content_text

_DELIVERY_HEADER = re.compile(
    r"(?:\A|\n\n)\[Tool ([^\]\n]+?) "
    r"(?:completed|failed(?:, exit [^\]\n]+)?|cancelled)\](?=\n|\Z)"
)
_BACKGROUND_PLACEHOLDER = re.compile(
    r"^(?:\[[^\]\n]+\] )?(?:Running in background — task delegated\.|"
    r"Promoted to background\. Result arrives in a later turn\.)"
)


def pending_snapshot_jobs(
    snapshot: list[dict], alias_owners: dict[str, set[str]]
) -> set[str]:
    """Prefer explicit job identities over potentially shared provider aliases."""
    pending = set()
    for message in snapshot:
        metadata = message.get("metadata") or {}
        if metadata.get("tool_result_phase") != "pending":
            continue
        explicit = metadata.get("job_id") or metadata.get("call_id")
        if explicit:
            owner = str(explicit)
            if owner in alias_owners.get(owner, set()):
                pending.add(owner)
            continue
        identities = {
            str(value)
            for value in (message.get("tool_call_id"), metadata.get("tool_call_id"))
            if value
        }
        for identity in identities:
            owners = alias_owners.get(identity, set())
            if len(owners) == 1:
                pending.update(owners)
    return pending


def _delivers_feedback(message: dict, metadata: dict) -> bool:
    """User messages that carry tool results: tagged feedback or a mid-turn batch."""
    return message.get("role") == "user" and (
        metadata.get("kind") == TOOL_FEEDBACK_KIND
        or metadata.get("origin_known") is False
    )


def result_identities(message: dict) -> set[str]:
    """Job and call identities whose result this snapshot message carries."""
    role = message.get("role")
    metadata = (
        message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    )
    names: set[str] = set()
    if role != "tool" and not _delivers_feedback(message, metadata):
        return names
    for key in ("job_id", "call_id"):
        if metadata.get(key):
            names.add(str(metadata[key]))
    job_ids = metadata.get("job_ids")
    if isinstance(job_ids, list):
        names.update(str(value) for value in job_ids if value)
    if names:
        return names
    if role == "tool":
        identity = str(message.get("tool_call_id") or "")
        return {identity} if identity else set()
    content = message.get("content") or ""
    texts = [render_content_text(content)]
    if isinstance(content, list):
        # A batch may lead with typed text, so each part can open a delivery.
        texts += [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
    for text in texts:
        names.update(_DELIVERY_HEADER.findall(text))
    return names


def result_is_final(message: dict) -> bool:
    """Whether a result-carrying message holds a settled result, not a placeholder."""
    metadata = (
        message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    )
    phase = metadata.get("tool_result_phase")
    if phase == "pending":
        return False
    if phase == "final":
        return True
    content = render_content_text(message.get("content") or "")
    if _BACKGROUND_PLACEHOLDER.match(content):
        return False
    if metadata.get("tool_results_elided") or ELISION_MARKER in content:
        return True
    if _delivers_feedback(message, metadata):
        return bool(result_identities(message))
    return True


def delivered_jobs(messages: list[dict]) -> set[str]:
    """Identities whose settled result already appears in these messages."""
    delivered: set[str] = set()
    for message in messages:
        identities = result_identities(message)
        if identities and result_is_final(message):
            delivered |= identities
    return delivered
