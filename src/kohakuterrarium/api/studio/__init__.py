"""Studio backend — embedded authoring studio for KohakuTerrarium.

Exposes a composite FastAPI router mounted at /api/studio/* and
/ws/studio/* by the core api app. The whole subtree is isolated
from the rest of the framework: core code never imports from
``kohakuterrarium.api.studio`` (enforced by
``tests/unit/test_studio_independence.py``).

Modules here may import freely from ``kohakuterrarium.core``,
``kohakuterrarium.builtins``, ``kohakuterrarium.modules``,
``kohakuterrarium.packages``, ``kohakuterrarium.llm``, and
``kohakuterrarium.serving`` — read-only dependencies documented in
``plans/kt-studio/architecture.md §9``.
"""

from kohakuterrarium.api.studio.app import build_studio_router

__all__ = ["build_studio_router"]
