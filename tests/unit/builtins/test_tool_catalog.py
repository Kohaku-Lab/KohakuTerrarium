"""Catalog lookup loads one tool while discovery and legacy exports stay complete."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kohakuterrarium.builtins import tool_catalog
from kohakuterrarium.builtins import tools
from kohakuterrarium.builtins.tool_manifest import TOOL_MODULES
from kohakuterrarium.modules.tool.base import BaseTool

ROOT = Path(__file__).resolve().parents[3]


def loaded_by(code):
    result = subprocess.run(
        [sys.executable, "-c", code + "\nprint(json.dumps(sorted(sys.modules)))"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    return set(json.loads(result.stdout))


@pytest.mark.parametrize(
    "code",
    [
        "import kohakuterrarium.builtins.tools",
        "from kohakuterrarium.builtins.tool_catalog import get_builtin_tool; "
        "assert get_builtin_tool('read').tool_name == 'read'",
        "from kohakuterrarium.builtins.tools import ReadTool; "
        "assert ReadTool().tool_name == 'read'",
        "from kohakuterrarium.builtins.tool_catalog import is_builtin_tool; "
        "assert not is_builtin_tool('not_a_real_tool')",
    ],
)
def test_lookup_and_package_exports_do_not_import_unrelated_runtimes(code):
    loaded = loaded_by("import json, sys\n" + code)
    for name in [
        "kohakuterrarium.builtins.tools.web.search",
        "kohakuterrarium.builtins.tools.media.grok_image_gen",
        "kohakuterrarium.llm.codex.provider",
        "kohakuterrarium.laboratory.remote_terrarium.deploy",
        "openai",
    ]:
        assert name not in loaded


def test_configured_web_search_does_not_import_sdk_or_controller_provider():
    loaded = loaded_by(
        "import json, sys\n"
        "from kohakuterrarium.builtins.tool_catalog import get_builtin_tool\n"
        "assert get_builtin_tool('web_search').tool_name == 'web_search'"
    )
    assert "openai" not in loaded
    assert "kohakuterrarium.llm.codex.provider" not in loaded
    assert "kohakuterrarium.builtins.tools.media.video_gen" not in loaded


def test_configurable_tool_discovery_is_complete_in_a_cold_process():
    loaded_by(
        "import json, sys\n"
        "from kohakuterrarium.builtins import tool_catalog as catalog\n"
        "cold = catalog.list_configurable_tools()\n"
        "assert {'web_search', 'web_fetch', 'image_gen'} <= {item['name'] for item in cold}\n"
        "catalog.list_builtin_tools()\n"
        "assert cold == catalog.list_configurable_tools()"
    )


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize(
    "operation",
    [
        "catalog.get_builtin_tool('python')",
        "catalog.list_builtin_tools()",
        "from kohakuterrarium.builtins.tools import PythonTool",
        "import kohakuterrarium.builtins.tools.python",
    ],
)
def test_cold_dependency_import_preserves_registered_overrides(deferred, operation):
    registration = (
        "catalog.register_deferred_loader(lambda: catalog.register_builtin('bash')(CustomBash))\n"
        "catalog.get_builtin_tool('unknown')\n"
        if deferred
        else "catalog.register_builtin('bash')(CustomBash)\n"
    )
    loaded_by(
        "import json, sys\n"
        "from kohakuterrarium.builtins import tool_catalog as catalog\n"
        "class CustomBash(type(catalog.get_builtin_tool('read'))):\n"
        "    pass\n"
        + registration
        + "assert type(catalog.get_builtin_tool('bash')) is CustomBash\n"
        + operation
        + "\nassert type(catalog.get_builtin_tool('bash')) is CustomBash\n"
    )


def test_class_exports_discovery_and_native_schema_remain_available():
    names = tool_catalog.list_builtin_tools()
    assert set(TOOL_MODULES) <= set(names)
    for name, (_, exported) in TOOL_MODULES.items():
        cls = getattr(tools, exported)
        assert cls is type(tool_catalog.get_builtin_tool(name))
        assert exported in dir(tools)
        assert exported in tools.__all__
    with pytest.raises(AttributeError, match="NoSuchTool"):
        getattr(tools, "NoSuchTool")
    assert tool_catalog.get_provider_native_option_schema("not_a_tool") == {}
    assert tool_catalog.get_provider_native_option_schema("image_gen") == {}
    assert tool_catalog.list_provider_native_tools() == []


def test_native_tool_schema_and_listing(native_image_gen):
    schema = tool_catalog.get_provider_native_option_schema("image_gen")
    assert schema["quality"]["default"] == "auto"
    assert "image_gen" in {
        item["name"] for item in tool_catalog.list_provider_native_tools()
    }


def test_deferred_registration_and_explicit_overrides_survive_lazy_loading(monkeypatch):
    monkeypatch.setattr(tool_catalog, "_BUILTIN_TOOLS", {})
    monkeypatch.setattr(tool_catalog, "_DEFERRED_LOADERS", [])
    calls = []

    class ExtensionTool(BaseTool):
        @property
        def tool_name(self):
            return "extension"

        @property
        def description(self):
            return "Extension tool"

        async def execute(self, args, context=None):
            return "extension result"

    def register():
        calls.append(True)
        tool_catalog.register_builtin("extension")(ExtensionTool)

    tool_catalog.register_deferred_loader(register)
    assert tool_catalog.get_builtin_tool("read").tool_name == "read"
    assert tool_catalog.get_builtin_tool("extension").tool_name == "extension"
    assert calls == [True]
    tool_catalog.register_builtin("read")(ExtensionTool)
    assert tool_catalog.get_builtin_tool("read").tool_name == "extension"
    assert tool_catalog.get_builtin_tool("unknown") is None
    assert calls == [True]
