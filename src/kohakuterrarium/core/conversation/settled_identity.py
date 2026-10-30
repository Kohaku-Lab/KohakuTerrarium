"""Resolve pending snapshot slots to unambiguous persisted jobs."""


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
