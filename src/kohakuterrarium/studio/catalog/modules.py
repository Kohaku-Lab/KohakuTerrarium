"""Module read-side primitives (list / load / load_doc).

Workspace-authored modules only. Catalog routes for
builtin / package / manifest entries live in
``studio.catalog.builtins`` and ``studio.catalog.catalog_sources``.
"""


def list_modules(ws, kind: str) -> list[dict]:
    """Return workspace-authored modules for *kind*.

    Manifest + package entries appear via the catalog routes — this
    surface is for the editor pool only.
    """
    return ws.list_modules(kind)


def load_module(ws, kind: str, name: str) -> dict:
    """Return the codegen-parsed envelope for ``(kind, name)``."""
    return ws.load_module(kind, name)


def load_module_doc(ws, kind: str, name: str) -> dict:
    """Return the sidecar skill-doc envelope for ``(kind, name)``."""
    return ws.load_module_doc(kind, name)
