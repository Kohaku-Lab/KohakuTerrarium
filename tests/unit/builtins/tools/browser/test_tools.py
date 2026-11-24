"""Unit tests for the six browser tools: argument validation and result shape.

Each tool runs against a ``browser`` plugin whose session uses the scripted
driver (``tests.helpers.browser_plugin``); no real browser is started.
"""

import base64
import json

import pytest

from kohakuterrarium.browser.errors import DriverError
from kohakuterrarium.builtin_skills import BUILTIN_SKILLS_DIR
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.builtins.tools.browser.common import NO_PLUGIN, files_list
from kohakuterrarium.builtins.tools.browser.screenshot import (
    MEDIA_POLICY,
    SCREENSHOT_MAX_BYTES,
)
from kohakuterrarium.llm.message import ImagePart, TextPart
from kohakuterrarium.llm.tools.schemas import _BUILTIN_SCHEMAS
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.modules.tool.media_policy import resolve_media_policy
from kohakuterrarium.skill_docs import load_skill_doc
from kohakuterrarium.utils.file.guard import PathBoundaryGuard
from tests.helpers.browser_driver import ScriptedDialog, png_bytes
from tests.helpers.browser_plugin import (
    call,
    plugin_context,
    scripted_plugin,
    tool_context,
)

TOOLS = (
    "browser_navigate",
    "browser_snapshot",
    "browser_act",
    "browser_screenshot",
    "browser_tabs",
    "browser_eval",
)


@pytest.fixture
async def env(tmp_path):
    plugin, driver = scripted_plugin()
    await plugin.on_load(plugin_context(tmp_path))

    async def run(name, args, **kwargs):
        return await call(plugin, name, args, tool_context(plugin, tmp_path, **kwargs))

    yield plugin, driver, run
    await plugin.on_unload()
    assert driver.alive() == []


def browser_meta(result) -> dict:
    return result.metadata["session_metadata"]["browser"]


class TestCommon:
    @pytest.mark.parametrize("name", TOOLS)
    def test_declarations(self, name):
        tool = get_builtin_tool(name)
        assert tool.needs_context and not tool.is_concurrency_safe
        assert tool.network_access is True
        assert tool.execution_mode.value == "direct"
        assert not tool.supports_background
        assert len(tool.description) <= 160
        doc = load_skill_doc(BUILTIN_SKILLS_DIR / "tools" / f"{name}.md")
        assert doc.description == tool.description
        assert name in _BUILTIN_SCHEMAS

    def test_path_and_url_declarations(self):
        assert get_builtin_tool("browser_act").path_arguments == {"files": "read"}
        assert get_builtin_tool("browser_screenshot").path_arguments == {
            "path": "write"
        }
        assert set(get_builtin_tool("browser_navigate").url_arguments) == {
            "url",
            "content",
        }
        assert get_builtin_tool("browser_tabs").url_arguments == ("url",)

    @pytest.mark.parametrize("name", TOOLS)
    async def test_without_the_plugin_every_tool_says_so(self, name, tmp_path):
        tool = get_builtin_tool(name)
        context = ToolContext(agent_name="a", session=None, working_dir=tmp_path)
        result = await tool.execute({}, context)
        assert result.error == NO_PLUGIN
        assert (await tool.execute({}, None)).error == NO_PLUGIN

    @pytest.mark.parametrize("name", TOOLS)
    async def test_unknown_arguments_are_named(self, env, name):
        _, driver, run = env
        result = await run(name, {"tab_id": "t1"})
        assert "unknown argument 'tab_id'" in result.error and "tab" in result.error
        assert driver.plans == []

    async def test_unexpected_failure_shows_no_exception_text(self, env):
        plugin, _, run = env

        async def boom(*args, **kwargs):
            raise RuntimeError("SECRET-INTERNAL details")

        plugin.session.snapshot = boom
        result = await run("browser_snapshot", {})
        assert "RuntimeError" in result.error
        assert "SECRET-INTERNAL" not in result.error

    def test_files_list_shapes(self):
        assert files_list(["a.txt", " b.txt "]) == ["a.txt", "b.txt"]
        assert files_list('["a.txt"]') == ["a.txt"]
        assert files_list("a.txt\nb.txt\n") == ["a.txt", "b.txt"]
        assert files_list(None) == [] and files_list("") == []
        with pytest.raises(ValueError):
            files_list("[not json")
        with pytest.raises(ValueError):
            files_list([1])


class TestNavigate:
    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ({}, "url is required for action goto; give a url, or set action"),
            ({"action": "goto", "url": ""}, "url is required for action goto"),
            ({"action": "sideways"}, "goto, back, forward, reload; got 'sideways'"),
            ({"url": 5}, "url must be a string"),
        ],
    )
    async def test_argument_errors_launch_nothing(self, env, args, message):
        _, driver, run = env
        result = await run("browser_navigate", args)
        assert message in result.error and result.error.startswith("browser_navigate")
        assert driver.plans == []

    async def test_opens_a_url_and_reports_tab_and_url(self, env):
        _, _, run = env
        result = await run("browser_navigate", {"url": "http://shop.test/next"})
        assert result.success
        assert result.output.startswith("[t1] http://shop.test/next\n<page_content")
        meta = browser_meta(result)
        assert meta["tool"] == "browser_navigate" and meta["ok"] is True
        assert meta["tab"] == "t1" and meta["url"] == "http://shop.test/next"

    async def test_url_as_the_call_body(self, env):
        _, driver, run = env
        result = await run("browser_navigate", {"content": "http://shop.test/body"})
        assert result.success
        assert ("goto", {"url": "http://shop.test/body", "timeout": 0.5}) in (
            driver.browser.page.calls
        )

    async def test_refused_scheme_is_an_error_naming_the_rule(self, env):
        _, driver, run = env
        result = await run("browser_navigate", {"url": "file:///etc/passwd"})
        assert "only http and https" in result.error
        assert driver.plans == []

    async def test_history_without_a_page_is_an_error(self, env):
        _, _, run = env
        result = await run("browser_navigate", {"action": "forward"})
        assert "no later page" in result.error


class TestSnapshot:
    async def test_filters_and_text_format_values(self, env):
        _, _, run = env
        result = await run(
            "browser_snapshot", {"interactive": "true", "depth": "2", "tab": "t1"}
        )
        assert result.success and 'button "Buy" [ref=e2]' in result.output
        assert 'heading "Shop"' not in result.output

    async def test_boxes_flag_reaches_the_driver(self, env):
        _, driver, run = env
        result = await run("browser_snapshot", {"boxes": "true"})
        assert result.success
        calls = driver.browser.page.calls
        assert (
            "snapshot",
            {"timeout": calls[-2][1]["timeout"], "boxes": True},
        ) in calls

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ({"boxes": "maybe"}, "boxes must be true or false"),
            ({"depth": "-1"}, "depth must be a whole number"),
            ({"depth": True}, "depth must be a whole number"),
            ({"interactive": "maybe"}, "interactive must be true or false"),
        ],
    )
    async def test_argument_errors(self, env, args, message):
        _, driver, run = env
        assert message in (await run("browser_snapshot", args)).error
        assert driver.plans == []

    async def test_unknown_ref_and_unknown_tab_are_errors(self, env):
        _, _, run = env
        result = await run("browser_snapshot", {"ref": "e99"})
        assert "ref e99 is not on the page now" in result.error
        result = await run("browser_snapshot", {"tab": "t9"})
        assert "t9" in result.error


class TestAct:
    @pytest.mark.parametrize("shape", ["list", "json", "body"])
    async def test_three_shapes_of_actions(self, env, shape):
        _, driver, run = env
        await run("browser_snapshot", {})
        actions = [{"kind": "click", "ref": "e2"}]
        args = {
            "list": {"actions": actions},
            "json": {"actions": json.dumps(actions)},
            "body": {"content": json.dumps(actions)},
        }[shape]
        result = await run("browser_act", args)
        assert result.success, result.error
        assert "1. click e2: done" in result.output
        meta = browser_meta(result)
        assert meta["actions"] == [
            {"index": 0, "kind": "click", "ref": "e2", "status": "done"}
        ]
        assert "click" in driver.browser.page.names()

    async def test_missing_or_invalid_actions(self, env):
        _, driver, run = env
        assert "actions is required" in (await run("browser_act", {})).error
        result = await run("browser_act", {"actions": "[]"})
        assert "actions is empty" in result.error
        result = await run("browser_act", {"actions": [{"kind": "fly"}]})
        assert "unknown kind 'fly'" in result.error
        assert driver.plans == []

    async def test_a_failed_action_is_an_error_with_statuses(self, env):
        _, _, run = env
        await run("browser_snapshot", {})
        result = await run(
            "browser_act",
            {
                "actions": [
                    {"kind": "click", "ref": "e2"},
                    {"kind": "click", "ref": "e9"},
                ]
            },
        )
        assert not result.success and "action" in result.error
        assert "1. click e2: done" in result.output
        assert "2. click e9: failed" in result.output
        statuses = [a["status"] for a in browser_meta(result)["actions"]]
        assert statuses == ["done", "failed"]
        assert browser_meta(result)["ok"] is False

    async def test_upload_files_are_resolved_and_must_exist(self, env, tmp_path):
        _, driver, run = env
        (tmp_path / "cv.pdf").write_bytes(b"%PDF")
        await run("browser_snapshot", {})
        result = await run(
            "browser_act",
            {
                "actions": [{"kind": "upload", "ref": "e3", "files": [0]}],
                "files": "cv.pdf",
            },
        )
        assert result.success, result.error
        (upload,) = [c for n, c in driver.browser.page.calls if n == "set_input_files"]
        assert upload["files"] == [str((tmp_path / "cv.pdf").resolve())]
        result = await run(
            "browser_act",
            {
                "actions": [{"kind": "upload", "ref": "e3", "files": [0]}],
                "files": ["missing.pdf"],
            },
        )
        assert "missing.pdf" in result.error and "not a file" in result.error

    async def test_upload_files_go_through_the_path_guard(self, env, tmp_path):
        _, driver, run = env
        outside = tmp_path.parent / "outside_upload.txt"
        outside.write_text("x")
        guard = PathBoundaryGuard(tmp_path, mode="block")
        result = await run(
            "browser_act",
            {
                "actions": [{"kind": "upload", "ref": "e3", "files": [0]}],
                "files": [str(outside)],
            },
            guard=guard,
        )
        assert "outside the working directory" in result.error
        assert driver.plans == []

    async def test_dialog_pending_result(self, env):
        _, driver, run = env
        await run("browser_snapshot", {})
        page = driver.browser.page

        async def click(**_):
            page.open_dialog(ScriptedDialog("confirm", "Sure?"))
            await page.browser.hung()

        page.handlers["click"] = click
        result = await run("browser_act", {"actions": [{"kind": "click", "ref": "e2"}]})
        assert "dialog pending" in result.output
        assert browser_meta(result)["dialog"] == "confirm"
        answer = await run("browser_act", {"actions": [{"kind": "dialog"}]})
        assert "1. dialog accept: done" in answer.output


class TestScreenshot:
    async def test_image_part_text_first_and_media_policy(self, env):
        _, driver, run = env
        result = await run("browser_screenshot", {})
        assert result.success
        text, image = result.output
        assert isinstance(text, TextPart) and isinstance(image, ImagePart)
        assert text.text.startswith("[t1] http://shop.test/")
        assert "Screenshot: 1280x800 CSS px, image/png" in text.text
        assert image.url.startswith("data:image/png;base64,")
        assert base64.b64decode(image.url.split(",", 1)[1]) == png_bytes(1280, 800)
        policy = resolve_media_policy(
            tool_of_name("browser_screenshot"), result.metadata
        )
        assert policy == MEDIA_POLICY and policy.inline_fallback and policy.persist
        (shot,) = [c for n, c in driver.browser.page.calls if n == "screenshot"]
        assert shot["full_page"] is False and shot["ref"] is None
        meta = browser_meta(result)
        assert meta["width"] == 1280 and meta["mime_type"] == "image/png"

    async def test_text_alone_is_still_useful(self, env):
        _, _, run = env
        result = await run("browser_screenshot", {})
        text = result.get_text_output()
        assert "1280x800" in text and "browser_snapshot" in text

    async def test_budget_is_passed_and_full_page_is_capped(self, env):
        _, driver, run = env
        await run("browser_screenshot", {"full_page": "true"})
        (shot,) = [c for n, c in driver.browser.page.calls if n == "screenshot"]
        assert shot["full_page"] is True and shot["max_height"] == 8000
        assert SCREENSHOT_MAX_BYTES == 512_000

    async def test_saves_to_a_guarded_path(self, env, tmp_path):
        _, _, run = env
        result = await run("browser_screenshot", {"path": "shots/page.png"})
        saved = tmp_path / "shots" / "page.png"
        assert saved.read_bytes() == png_bytes(1280, 800)
        assert str(saved.resolve()) in result.output[0].text
        assert browser_meta(result)["saved_to"] == str(saved.resolve())
        guard = PathBoundaryGuard(tmp_path, mode="block")
        result = await run(
            "browser_screenshot", {"path": str(tmp_path.parent / "x.png")}, guard=guard
        )
        assert "outside the working directory" in result.error

    async def test_ref_wins_over_full_page(self, env):
        _, driver, run = env
        await run("browser_snapshot", {})
        result = await run("browser_screenshot", {"ref": "e2", "full_page": True})
        assert result.error is None
        shot = [k for n, k in driver.browser.page.calls if n == "screenshot"][-1]
        assert shot["ref"] == "e2" and not shot["full_page"]

    async def test_stale_ref_is_an_error_without_an_image(self, env):
        _, driver, run = env
        await run("browser_snapshot", {})
        driver.browser.page.snapshot_text = driver.browser.page.snapshot_text.replace(
            '"Buy"', '"Sell"'
        )
        result = await run("browser_screenshot", {"ref": "e2"})
        assert not result.success and isinstance(result.output, str)
        assert "points at a different element" in result.output


class TestTabs:
    async def test_list_before_any_browser_does_not_launch(self, env):
        _, driver, run = env
        result = await run("browser_tabs", {})
        assert "No browser is running" in result.output
        assert browser_meta(result)["tabs"] == [] and driver.plans == []

    async def test_new_select_close_and_close_browser(self, env):
        _, driver, run = env
        await run("browser_snapshot", {})
        result = await run("browser_tabs", {"action": "new", "url": "http://b.test/"})
        assert result.success and browser_meta(result)["tab"] == "t2"
        result = await run("browser_tabs", {"action": "select", "tab": "t1"})
        assert browser_meta(result)["active"] == "t1"
        result = await run("browser_tabs", {"action": "close", "tab": "t2"})
        assert [t["tab"] for t in browser_meta(result)["tabs"]] == ["t1"]
        result = await run("browser_tabs", {"content": "close_browser"})
        assert browser_meta(result)["closed"] is True and driver.alive() == []

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ({"action": "jump"}, "action must be one of"),
            ({"action": "select"}, "select needs tab"),
            (
                {"action": "list", "url": "http://a.test/"},
                'url applies to action new; set url to ""',
            ),
            ({"action": "list", "tab": "t1"}, "tab applies to action select or close"),
        ],
    )
    async def test_argument_errors(self, env, args, message):
        _, driver, run = env
        assert message in (await run("browser_tabs", args)).error
        assert driver.plans == []


class TestEval:
    async def test_result_is_json_inside_the_wrapper(self, env):
        _, driver, run = env
        await run("browser_snapshot", {})
        driver.browser.page.eval_value = {"count": 3}
        result = await run("browser_eval", {"expression": "() => ({count: 3})"})
        assert result.success and '{"count": 3}' in result.output
        assert result.output.index("<page_content") < result.output.index("count")

    async def test_expression_as_body_and_with_a_ref(self, env):
        _, driver, run = env
        await run("browser_snapshot", {})
        await run("browser_eval", {"content": "el => el.id", "ref": "e2"})
        (evaluated,) = [c for n, c in driver.browser.page.calls if n == "evaluate"]
        assert evaluated["expression"] == "el => el.id" and evaluated["ref"] == "e2"

    async def test_missing_expression(self, env):
        _, driver, run = env
        assert "expression is required" in (await run("browser_eval", {})).error
        assert driver.plans == []

    async def test_a_thrown_error_is_an_error_result(self, env):
        _, driver, run = env
        await run("browser_snapshot", {})
        driver.browser.page.raises["evaluate"] = DriverError(
            "the script threw", detail="ReferenceError: x"
        )
        result = await run("browser_eval", {"expression": "x"})
        assert not result.success and "ReferenceError: x" in result.output


def tool_of_name(name):
    return get_builtin_tool(name)
