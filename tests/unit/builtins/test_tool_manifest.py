"""The lazy tool manifest covers every default registration and class export."""

import ast
from pathlib import Path

from kohakuterrarium.builtins.tool_manifest import TOOL_MODULES

ROOT = Path(__file__).resolve().parents[3]


def test_manifest_matches_default_tool_registration_sources():
    package = ROOT / "src" / "kohakuterrarium"
    expected = {}
    aliases = {}
    files = [
        *(package / "builtins" / "tools").rglob("*.py"),
        package / "mcp" / "tools.py",
    ]
    for file in files:
        module = ".".join(file.relative_to(package).with_suffix("").parts)
        for node in ast.parse(file.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[module, target.id] = node.value.id
            if not isinstance(node, ast.ClassDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "register_builtin"
                ):
                    expected[decorator.args[0].value] = module, node.name
    assert set(TOOL_MODULES) == set(expected)
    for name, (module, exported) in TOOL_MODULES.items():
        assert (module, aliases.get((module, exported), exported)) == expected[name]
