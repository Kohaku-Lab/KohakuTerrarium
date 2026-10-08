"""Checking a module loads it as a creature would and reports why it cannot."""

import pytest

from kohakuterrarium.studio.editors.module_check import check_module
from kohakuterrarium.studio.editors.starters import list_starters
from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace


@pytest.fixture
def ws(tmp_path):
    return LocalWorkspace.open(tmp_path)


def _write(ws, kind, name, source):
    path = ws.module_kind_dir(kind) / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_every_starter_checks_clean(ws):
    for starter in list_starters():
        if starter.kind == "creatures":
            continue
        name = f"{starter.kind}_{starter.id}"
        ws.scaffold_module(starter.kind, name, starter.id)
        result = check_module(ws, starter.kind, name)
        assert result["ok"], (name, result)
        assert result["loaded"]


def test_reports_a_syntax_error_with_its_line(ws):
    _write(ws, "tools", "bad", "x = 1\ndef (:\n")
    result = check_module(ws, "tools", "bad")
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "syntax_error"
    assert result["errors"][0]["line"] == 2


def test_reports_a_failing_import_or_constructor(ws):
    _write(ws, "tools", "imp", "import not_a_real_module_xyz\nclass T:\n    pass\n")
    result = check_module(ws, "tools", "imp")
    assert result["errors"][0]["code"] == "load_failed"
    assert "not_a_real_module_xyz" in result["errors"][0]["message"]
    _write(
        ws,
        "plugins",
        "boom",
        "class P:\n    def __init__(self):\n        raise RuntimeError('nope')\n",
    )
    result = check_module(ws, "plugins", "boom")
    assert result["errors"][0]["message"] == "RuntimeError: nope"


def test_reports_the_wrong_base_type_and_nothing_to_load(ws):
    _write(ws, "tools", "plain", "class NotATool:\n    pass\n")
    result = check_module(ws, "tools", "plain")
    assert result["errors"][0] == {
        "code": "wrong_type",
        "message": "NotATool is not a BaseTool",
    }
    _write(ws, "subagents", "empty", "X = 1\n")
    assert (
        check_module(ws, "subagents", "empty")["errors"][0]["code"] == "nothing_to_load"
    )


def test_a_missing_module_is_not_found(ws):
    with pytest.raises(FileNotFoundError):
        check_module(ws, "tools", "ghost")
