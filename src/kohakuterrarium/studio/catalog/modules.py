"""Module read-side primitives (list / load / load_doc).

Exposes only modules authored in the active workspace. Built-in, package, and
manifest-declared entries are aggregated separately so the editor pool remains a
faithful view of locally editable files.
"""


def list_modules(ws, kind: str) -> list[dict]:
    """Return locally editable modules for *kind*."""
    return ws.list_modules(kind)


def load_module(ws, kind: str, name: str) -> dict:
    """Return the parsed source envelope for one workspace module."""
    return ws.load_module(kind, name)


def load_module_doc(ws, kind: str, name: str) -> dict:
    """Return the documentation sidecar envelope for one workspace module."""
    return ws.load_module_doc(kind, name)
