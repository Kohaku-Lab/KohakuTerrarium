"""Module CRUD primitives (scaffold / save / save_doc / delete).

Uses the per-kind code-generation dispatcher for scaffolding and structured
updates. ``LocalWorkspace`` resolves workspace-native or manifest-declared paths
before delegating here.
"""

from pathlib import Path

from kohakuterrarium.studio.editors.codegen_common import RoundTripError
from kohakuterrarium.studio.editors.codegen_init import get_codegen
from kohakuterrarium.studio.editors.utils_paths import sanitize_name
from kohakuterrarium.studio.editors.workspace_manifest import (
    save_sidecar_doc,
    write_codegen_sidecars,
)


def scaffold_module(kind_dir: Path, kind: str, name: str, template: str | None) -> Path:
    """Scaffold a new Python module and return its path.

    Existing destinations raise ``FileExistsError``.
    """
    name = sanitize_name(name)
    kind_dir.mkdir(parents=True, exist_ok=True)
    path = kind_dir / f"{name}.py"
    if path.exists():
        raise FileExistsError(f"{kind}/{name}")

    cg = get_codegen(kind)
    singular = kind[:-1] if kind.endswith("s") else kind
    source = cg.render_new(
        {
            "name": name,
            "template": template,
            "kind": singular,
        }
    )
    path.write_text(source, encoding="utf-8")
    return path


def save_module(
    kind: str,
    name: str,
    data: dict,
    *,
    existing_path: Path | None,
    fallback_path: Path,
) -> Path:
    """Persist a module in raw-source or structured form mode.

    Existing modules are updated in place; new modules use ``fallback_path``.
    Invalid modes and empty raw source raise ``ValueError``. Unsafe structured
    rewrites propagate ``RoundTripError``.
    """
    name = sanitize_name(name)
    cg = get_codegen(kind)

    path = existing_path or fallback_path

    mode = data.get("mode", "simple")
    form = data.get("form") or {}
    if mode == "raw":
        raw = data.get("raw_source", "")
        if not raw:
            raise ValueError("raw_source required in raw mode")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
    elif mode == "simple":
        exec_body = data.get("execute_body", "")
        if path.exists():
            try:
                new_src = cg.update_existing(
                    path.read_text(encoding="utf-8"), form, exec_body
                )
            except RoundTripError:
                raise
        else:
            new_src = cg.render_new(
                {
                    **form,
                    "name": name,
                    "execute_body": exec_body,
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_src, encoding="utf-8")
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    write_codegen_sidecars(cg, form, path)
    return path


def save_module_doc(py_path: Path, content: str) -> None:
    """Write a module's Markdown documentation sidecar."""
    save_sidecar_doc(py_path, content)


def delete_module(kind: str, name: str, path: Path | None) -> None:
    """Delete a module file, raising ``FileNotFoundError`` when absent."""
    sanitize_name(name)
    if path is None:
        raise FileNotFoundError(f"{kind}/{name}")
    path.unlink()
