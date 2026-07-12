"""Unit coverage for durable Drive split-intent recovery."""

import pytest

from kohakuterrarium.terrarium.drive.split_intent import (
    recover_split_intents,
    write_split_intent,
)


def _empty_payload() -> dict:
    return {
        "wire_schema": 1,
        "drives": [],
        "assignments": [],
        "deliveries": [],
        "audit": [],
        "progress": [],
        "outbox": [],
        "dead_letters": [],
        "idempotency": [],
        "proposals": [],
    }


@pytest.mark.asyncio
async def test_split_intent_recovers_exact_repository_paths_and_cancels_deleted_sessions(
    tmp_path,
):
    session_a = tmp_path / "a.kohakutr"
    session_b = tmp_path / "b.kohakutr"
    session_a.touch()
    session_b.touch()
    repo_a = tmp_path / "provider-a.db"
    repo_b = tmp_path / "provider-b.db"
    payloads = {"a": _empty_payload(), "b": _empty_payload()}

    intent = write_split_intent(
        str(tmp_path / "source.kohakutr.drives"),
        {"a": str(repo_a), "b": str(repo_b)},
        {"a": str(session_a), "b": str(session_b)},
        payloads,
    )
    assert intent.name == "source.kohakutr.drives.split-intent.json"
    assert await recover_split_intents(tmp_path) == 1
    assert not intent.exists()
    assert repo_a.is_file() and repo_b.is_file()

    deleted_repo = tmp_path / "deleted-provider.db"
    cancelled = write_split_intent(
        str(tmp_path / "deleted.kohakutr.drives"),
        {"deleted": str(deleted_repo)},
        {"deleted": str(tmp_path / "deleted.kohakutr")},
        {"deleted": _empty_payload()},
    )
    assert await recover_split_intents(tmp_path) == 0
    assert not cancelled.exists()
    assert not deleted_repo.exists()
