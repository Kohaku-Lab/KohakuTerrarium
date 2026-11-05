"""Pin persisted module defaults, field isolation, and validation."""

import json

import pytest

from kohakuterrarium.studio.identity.module_defaults import (
    defaults_path,
    list_defaults,
    load_defaults,
    patch_defaults,
)
from kohakuterrarium.utils.file.lock import FileLock, FileLockBusy

pytestmark = pytest.mark.usefixtures("native_image_gen")


def test_catalog_and_partial_saves_preserve_other_defaults():
    assert load_defaults() == {}
    catalog = {(m["type"], m["name"]): m for m in list_defaults()}
    assert catalog["tool", "web_search"]["options"]["backend"] == "duckduckgo"
    assert "quality" in catalog["native_tool", "image_gen"]["schema"]
    patch_defaults("tool", "web_search", {"fallback": "duckduckgo"})
    patch_defaults("tool", "web_search", {"codex_model": "gpt-6.1-sol"})
    patch_defaults("native_tool", "image_gen", {"quality": "high"})
    patch_defaults(
        "plugin", "budget", {"turn_budget": {"soft": 20, "hard": 40}}, enabled=True
    )
    result = patch_defaults("plugin", "budget", {"walltime_budget": {"hard": 300}})
    assert result["enabled"] is True
    assert result["options"]["turn_budget"] == {"soft": 20, "hard": 40}
    assert result["options"]["walltime_budget"] == {"hard": 300}
    result = patch_defaults("tool", "web_search", {"codex_model": None})
    assert result["options"]["fallback"] == "duckduckgo"
    saved = load_defaults()
    assert "codex_model" not in saved["tool"]["web_search"]["options"]
    assert saved["native_tool"]["image_gen"]["options"] == {"quality": "high"}
    assert saved["plugin"]["budget"]["enabled"] is True


@pytest.mark.parametrize(
    "kind,name,values,enabled,error",
    [
        ("tool", "missing", {}, None, KeyError),
        ("other", "web_search", {}, None, KeyError),
        ("tool", "web_search", {"backend": "missing"}, None, ValueError),
        ("tool", "web_fetch", {}, True, ValueError),
        ("plugin", "budget", {"missing": 5}, None, ValueError),
        ("plugin", "budget", {"turn_budget": {"hard": "abc"}}, None, ValueError),
        (
            "plugin",
            "budget",
            {"walltime_budget": {"soft": "abc", "hard": 300}},
            None,
            ValueError,
        ),
        (
            "plugin",
            "budget",
            {"tool_call_budget": {"hard": float("inf")}},
            None,
            ValueError,
        ),
        ("native_tool", "image_gen", {"size": "1x1"}, None, ValueError),
    ],
)
def test_invalid_patch_leaves_file_unchanged(kind, name, values, enabled, error):
    patch_defaults("tool", "web_search", {"fallback": "duckduckgo"})
    before = defaults_path().read_bytes()
    with pytest.raises(error):
        patch_defaults(kind, name, values, enabled=enabled)
    assert defaults_path().read_bytes() == before


def test_contending_writer_and_corrupt_file_are_reported():
    patch_defaults("tool", "web_fetch", {"fallback": "auto"})
    before = defaults_path().read_bytes()
    with FileLock(defaults_path().with_suffix(".lock")):
        with pytest.raises(FileLockBusy):
            patch_defaults("tool", "web_search", {"fallback": "duckduckgo"})
    assert defaults_path().read_bytes() == before
    defaults_path().write_text("[]")
    with pytest.raises(ValueError, match="must be an object"):
        load_defaults()
    defaults_path().write_text("{")
    with pytest.raises(json.JSONDecodeError):
        load_defaults()
