"""Studio backend — embedded authoring studio for KohakuTerrarium.

Exposes the composite FastAPI router mounted by the core API application.
This subtree may depend on framework packages, but framework code must not
import it; that one-way dependency keeps Studio isolated from the runtime core.
"""

from kohakuterrarium.api.studio.app import build_studio_router

__all__ = ["build_studio_router"]
