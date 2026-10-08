"""Check a workspace module the way a creature would load it.

The saved file is compiled, then loaded through :class:`ModuleLoader` with the
same entry a creature config uses: the class is instantiated with no options
(the sub-agent config object is read), and must be the kind's base type.
Loading runs the module's top-level code, as starting a creature does.
"""

from typing import Any

from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.modules.input.base import BaseInputModule
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.tool.base import BaseTool
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.studio.editors.workspace_wiring import module_wiring

BASE_TYPES: dict[str, type] = {
    "tools": BaseTool,
    "plugins": BasePlugin,
    "triggers": BaseTrigger,
    "inputs": BaseInputModule,
    "outputs": BaseOutputModule,
    "subagents": SubAgentConfig,
}


def _fail(code: str, message: str, line: int | None = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if line is not None:
        error["line"] = line
    return {"ok": False, "errors": [error], "loaded": None}


def check_module(ws: Any, kind: str, name: str) -> dict:
    """``{ok, errors, loaded}`` for the saved module ``kind/name``."""
    info = module_wiring(ws, kind, name)
    path = ws._find_module_file(kind, name).resolve()
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as e:
        return _fail("syntax_error", str(e.msg), e.lineno)

    entry = info["entry"]
    loader = ModuleLoader()
    target = entry.get("config") if kind == "subagents" else entry.get("class")
    if not target:
        missing = "a SubAgentConfig" if kind == "subagents" else "a class"
        return _fail("nothing_to_load", f"{path.name} defines no {missing}")
    try:
        if kind == "subagents":
            loaded = loader.load_config_object(str(path), target)
        else:
            loaded = loader.load_instance(str(path), target)
    except Exception as e:  # the module's own import or constructor failed
        cause = e.__cause__ or e
        return _fail("load_failed", f"{type(cause).__name__}: {cause}")
    base = BASE_TYPES[kind]
    if not isinstance(loaded, base):
        return _fail("wrong_type", f"{target} is not a {base.__name__}")
    return {"ok": True, "errors": [], "loaded": target}
