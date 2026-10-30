"""Job execution records."""

import time
from typing import Any


class JobRecordsMixin:
    """Persist one record per job id."""

    # Table opened by the handle part.
    jobs: Any

    def save_job(self, job_id: str, data: dict) -> None:
        """Save a job execution record."""
        if "ts" not in data:
            data["ts"] = time.time()
        self.jobs[job_id] = data

    def load_job(self, job_id: str) -> dict | None:
        """Load a job record."""
        try:
            return self.jobs[job_id]
        except KeyError:
            return None
