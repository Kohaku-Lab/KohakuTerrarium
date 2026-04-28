"""Shared stub for codegen kinds whose full implementation lands in Phase 3.

Phase 1 exposes just enough for the read-only route to work: a
``parse_back`` that returns raw-mode envelope with a warning, and
``render_new``/``update_existing`` that simply pass source through
(valid raw-mode behavior — no round-trip attempt).

Per-kind modules re-export these via ``from codegen_pending import *``
and override as implementation matures.
"""

PENDING_WARNING = {
    "code": "codegen_pending",
    "message": "form-mode codegen for this kind lands in Phase 3; use raw mode",
}


def render_new_stub(form: dict, *, header_comment: str = "") -> str:
    """Scaffold a minimal placeholder file.

    Used only when the form has no real implementation yet (Phase 1).
    Phase 3 per-kind ``render_new`` replaces this.
    """
    name = form.get("name", "module")
    return (
        f'"""{header_comment or f"{name} — TODO: implement"}"""\n\n'
        f"# Placeholder scaffolded by studio. Replace with real code.\n"
    )


def update_existing_stub(source: str, form: dict, execute_body: str) -> str:
    """Pass source through unchanged — raw mode writes use a different path."""
    return source


def parse_back_stub(source: str) -> dict:
    """Always raw mode with a pending warning."""
    return {
        "mode": "raw",
        "form": {},
        "execute_body": "",
        "warnings": [PENDING_WARNING],
    }
