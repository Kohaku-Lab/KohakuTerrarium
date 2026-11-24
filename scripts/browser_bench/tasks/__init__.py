"""Every benchmark task, in report order, and lookup by id."""

from ..model import Task
from . import commerce, data, forms, frames, infeasible, injection, interaction, safety

TASKS: tuple[Task, ...] = (
    *forms.TASKS,
    *data.TASKS,
    *frames.TASKS,
    *interaction.TASKS,
    *commerce.TASKS,
    *infeasible.TASKS,
    *injection.TASKS,
    *safety.TASKS,
)
BY_ID: dict[str, Task] = {task.id: task for task in TASKS}

__all__ = ["BY_ID", "TASKS"]
