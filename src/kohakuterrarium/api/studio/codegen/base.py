"""Shared types for Studio codegen modules."""

from typing import Protocol


class RoundTripError(ValueError):
    """Raised when an AST-based round-trip cannot preserve the file."""


class Codegen(Protocol):
    """Protocol implemented by each per-kind codegen module."""

    def render_new(self, form: dict) -> str: ...
    def update_existing(self, source: str, form: dict, execute_body: str) -> str: ...
    def parse_back(self, source: str) -> dict: ...
