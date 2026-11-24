"""Browser-agent micro-benchmark: a local task site, ~30 tasks, checks and oracles.

The seam for a harness: :func:`running_site` starts the site and yields a
:class:`BenchSite` (``site.urls``, ``site.url(task)``, ``site.reset``,
``site.state``); :data:`TASKS` is the task list; :func:`check` judges one run
from ``site.state(task.id)``. See ``README.md``.
"""

from .model import CheckResult, Task, Urls, check
from .site import BenchSite, running_site
from .tasks import BY_ID, TASKS

__all__ = [
    "BY_ID",
    "BenchSite",
    "CheckResult",
    "TASKS",
    "Task",
    "Urls",
    "check",
    "running_site",
]
