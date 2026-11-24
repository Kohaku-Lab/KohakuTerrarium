"""Scripted oracle and adversary runs for every task."""

from . import commerce, data, forms, frames, infeasible, injection, interaction, safety
from .runs import Run

RUNS: tuple[Run, ...] = (
    *forms.RUNS,
    *data.RUNS,
    *frames.RUNS,
    *interaction.RUNS,
    *commerce.RUNS,
    *infeasible.RUNS,
    *injection.RUNS,
    *safety.RUNS,
)

__all__ = ["RUNS", "Run"]
