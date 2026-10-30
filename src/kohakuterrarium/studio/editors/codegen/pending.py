"""Raw-mode fallback for codegen kinds without structured support.

Provides raw-mode fallbacks for module kinds without structured round-trip
support. Parsing emits a warning, updates preserve source, and scaffolding creates
a minimal placeholder.
"""

PENDING_WARNING = {
    "code": "codegen_pending",
    "message": "form-mode codegen for this kind lands in Phase 3; use raw mode",
}


def render_new_stub(form: dict, *, header_comment: str = "") -> str:
    """Scaffold a minimal placeholder for a raw-mode-only module kind."""
    name = form.get("name", "module")
    return (
        f'"""{header_comment or f"{name} — TODO: implement"}"""\n\n'
        f"# Placeholder scaffolded by studio. Replace with real code.\n"
    )


def update_existing_stub(source: str, form: dict, execute_body: str) -> str:
    """Preserve source because raw-mode writes bypass structured updates."""
    return source


def parse_back_stub(source: str) -> dict:
    """Return a raw-mode envelope explaining the missing form support."""
    return {
        "mode": "raw",
        "form": {},
        "execute_body": "",
        "warnings": [PENDING_WARNING],
    }
