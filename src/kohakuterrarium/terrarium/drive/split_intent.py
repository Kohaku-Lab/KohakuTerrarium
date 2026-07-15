"""Durable journal for completing session-backed graph splits after interruption."""

import json
import os
from pathlib import Path
from uuid import uuid4

from kohakuterrarium.terrarium.drive.store import SqliteDriveRepository
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_SUFFIX = ".split-intent.json"


def write_split_intent(
    source_sidecar: str,
    repositories: dict[str, str],
    sessions: dict[str, str],
    payloads: dict[str, dict],
) -> Path:
    """Persist a replayable split before publishing any child repository.

    The temporary file is flushed and atomically replaced so recovery never sees
    a partially written intent.
    """
    path = Path(source_sidecar + _SUFFIX)
    data = {
        "version": 1,
        "operation_id": uuid4().hex,
        "source_sidecar": source_sidecar,
        "repositories": repositories,
        "sessions": sessions,
        "payloads": payloads,
    }
    tmp = path.with_name(f"{path.name}.tmp-{uuid4().hex}")
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    try:
        with open(tmp, "xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def complete_split_intent(path: str | Path) -> None:
    Path(path).unlink(missing_ok=True)


async def recover_split_intents(directory: str | Path) -> int:
    """Roll unfinished split intents forward into every surviving child.

    Intents whose session files no longer exist are discarded because their
    child repositories are no longer publishable.
    """
    root = Path(directory)
    if not root.is_dir():
        return 0
    recovered = 0
    for path in root.glob(f"*{_SUFFIX}"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sessions = data["sessions"]
            if any(
                not Path(session_path).is_file() for session_path in sessions.values()
            ):
                complete_split_intent(path)
                continue
            repositories = data["repositories"]
            payloads = data["payloads"]
            for graph_id, repository_path in repositories.items():
                repo = SqliteDriveRepository(repository_path)
                try:
                    await repo.replace_rows(payloads[graph_id])
                finally:
                    repo.close_blocking()
            complete_split_intent(path)
            recovered += 1
        except Exception:
            logger.exception("Drive split intent recovery failed", intent=str(path))
            raise
    return recovered


__all__ = ["complete_split_intent", "recover_split_intents", "write_split_intent"]
