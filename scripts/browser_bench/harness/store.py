"""The output directory: ``runs.jsonl`` (one record per run), ``meta.json``, transcripts.

Records are appended as runs finish, so an interrupted run resumes: a rerun
skips every task/repeat pair that already has a judged record of the same
model, and retries pairs whose latest record is a harness error.
"""

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

RUNS_FILE = "runs.jsonl"
META_FILE = "meta.json"
TRANSCRIPTS_DIR = "transcripts"
META_KEYS = ("model", "effort")


def latest(records: Iterable[Mapping[str, Any]]) -> dict[tuple[str, int], dict]:
    """The last record of each ``(task_id, repeat)`` pair, in file order."""
    out: dict[tuple[str, int], dict] = {}
    for record in records:
        out[(record["task_id"], int(record["repeat"]))] = dict(record)
    return out


def plan(
    task_ids: Iterable[str],
    repeats: int,
    records: Iterable[Mapping[str, Any]],
    *,
    force: bool,
    model: str | None = None,
) -> list[tuple[str, int]]:
    """The ``(task_id, repeat)`` pairs still to run, in task then repeat order."""
    mine = [r for r in records if model is None or r.get("model") == model]
    done = {
        key
        for key, record in latest(mine).items()
        if record.get("outcome") != "harness_error"
    }
    return [
        (task_id, repeat)
        for task_id in dict.fromkeys(task_ids)
        for repeat in range(repeats)
        if force or (task_id, repeat) not in done
    ]


class RunStore:
    """Reads and appends run records and places transcripts under ``out``."""

    def __init__(self, out: Path) -> None:
        self.out = Path(out)
        self.runs_path = self.out / RUNS_FILE
        self.meta_path = self.out / META_FILE
        self.transcripts_dir = self.out / TRANSCRIPTS_DIR
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[dict[str, Any]]:
        """Every complete record; a torn trailing line from a killed run is skipped."""
        if not self.runs_path.exists():
            return []
        records = []
        for line in self.runs_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def append(self, record: Mapping[str, Any]) -> None:
        text = (
            self.runs_path.read_text(encoding="utf-8")
            if self.runs_path.exists()
            else ""
        )
        prefix = "\n" if text and not text.endswith("\n") else ""
        with self.runs_path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + json.dumps(record, ensure_ascii=False) + "\n")

    def check_meta(self, meta: Mapping[str, Any], *, force: bool) -> None:
        """Record the model settings; refuse to mix settings in one directory unless forced."""
        if self.meta_path.exists() and not force:
            old = json.loads(self.meta_path.read_text(encoding="utf-8"))
            for key in META_KEYS:
                if key in old and old[key] != meta.get(key):
                    raise ValueError(
                        f"{self.out} holds runs with {key}={old[key]!r}, not "
                        f"{meta.get(key)!r}; use another --out or --force"
                    )
        self.meta_path.write_text(json.dumps(dict(meta), indent=2), encoding="utf-8")

    def transcript_path(self, task_id: str, repeat: int, suffix: str) -> Path:
        return self.transcripts_dir / f"{task_id}__r{repeat}{suffix}"
