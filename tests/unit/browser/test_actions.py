"""Unit tests for ``kohakuterrarium.browser.actions``: parsing, validation, batches."""

import asyncio
import json

import pytest

from kohakuterrarium.browser.actions import (
    BATCH_MIN_S,
    BATCH_SLOW_ACTIONS,
    DONE,
    FAILED,
    MAX_ACTIONS,
    PENDING,
    SKIPPED,
    WAIT_MAX_S,
    Action,
    BatchReport,
    Outcome,
    batch_deadline_s,
    format_details,
    format_report,
    parse_actions,
    run_batch,
)
from kohakuterrarium.browser.errors import (
    ActionFailedError,
    ActionSpecError,
    DialogOpenedError,
    DialogPendingError,
    DriverDisconnectedError,
    DriverError,
    PageClosedError,
)
from kohakuterrarium.errors import InvalidRequestError

CLICK = {"kind": "click", "ref": "e5"}


class TestParseShapes:
    def test_list_of_mappings(self):
        [action] = parse_actions([CLICK])
        assert action == Action(index=0, kind="click", ref="e5")

    def test_json_string(self):
        assert parse_actions(json.dumps([CLICK]))[0].ref == "e5"

    def test_single_mapping_and_actions_wrapper(self):
        assert parse_actions(CLICK)[0].kind == "click"
        assert parse_actions({"actions": [CLICK, CLICK]})[1].index == 1
        assert parse_actions('{"actions": [{"kind": "press", "key": "Enter"}]}')[
            0
        ].key == ("Enter")

    def test_call_body_with_prose_and_fences(self):
        body = 'Clicking now:\n```json\n[{"kind": "click", "ref": "f1e3"}]\n```\n'
        assert parse_actions(body)[0].ref == "f1e3"
        body = 'actions = [{"kind": "hover", "ref": "e2"}] then done'
        assert parse_actions(body)[0].kind == "hover"

    def test_action_alias_for_kind(self):
        assert parse_actions([{"action": "hover", "ref": "e1"}])[0].kind == "hover"

    @pytest.mark.parametrize(
        "value,message",
        [
            ("no json here", "must be a JSON list"),
            ("[{broken", "not valid JSON"),
            ([], "is empty"),
            (42, "must be a list"),
            ([CLICK] * (MAX_ACTIONS + 1), f"at most {MAX_ACTIONS} actions"),
            (["click"], r"actions\[0\]: each action must be an object"),
        ],
    )
    def test_shape_errors(self, value, message):
        with pytest.raises(ActionSpecError, match=message) as caught:
            parse_actions(value)
        assert isinstance(caught.value, InvalidRequestError)


class TestValidation:
    @pytest.mark.parametrize(
        "item,message",
        [
            ({"ref": "e1"}, r"actions\[0\]: missing 'kind'"),
            ({"kind": "tap"}, r"unknown kind 'tap'; one of click, type"),
            (
                {"kind": "click", "ref": "e1", "value": 1},
                r"actions\[0\] \(click\): unknown field 'value'; allowed:",
            ),
            ({"kind": "click"}, r"field 'ref' is required \(or 'x' and 'y'"),
            ({"kind": "click", "ref": "button"}, "must be a ref from a snapshot"),
            ({"kind": "click", "x": 1}, "field 'y' is required"),
            ({"kind": "click", "x": -5, "y": 1}, "between 0 and 100000"),
            ({"kind": "click", "x": "a", "y": 1}, "must be a number"),
            ({"kind": "click", "x": True, "y": 1}, "must be a number"),
            ({"kind": "click", "ref": "e1", "button": "side"}, "one of left"),
            ({"kind": "click", "ref": "e1", "double": "yes"}, "true or false"),
            ({"kind": "click", "ref": "e1", "modifiers": ["Hyper"]}, "modifiers"),
            ({"kind": "type", "ref": "e1", "text": 5}, "must be a string"),
            ({"kind": "type", "ref": "e1", "text": 5}, "must be a string"),
            ({"kind": "type", "ref": "e1", "text": "x" * 10001}, "longer than"),
            ({"kind": "select", "ref": "e1"}, "non-empty list of option labels"),
            ({"kind": "select", "ref": "e1", "values": [1]}, "option labels"),
            ({"kind": "press"}, "field 'key' is required"),
            ({"kind": "press", "key": ""}, "field 'key' is required"),
            ({"kind": "click", "ref": "e1", "key": "a"}, "does not apply to click"),
            ({"kind": "drag", "ref": "e1"}, r"field 'to' is required \(or 'to_x'"),
            ({"kind": "drag", "to": "e1"}, r"field 'ref' is required \(or 'x' and 'y'"),
            ({"kind": "drag", "ref": "e1", "to_x": 1}, "field 'to_y' is required"),
            ({"kind": "hover"}, r"field 'ref' is required \(or 'x' and 'y'"),
            ({"kind": "hover", "x": 1}, "field 'y' is required"),
            ({"kind": "upload", "ref": "e1"}, "indexes into the call's 'files'"),
            ({"kind": "upload", "ref": "e1", "files": ["a"]}, "integer indexes"),
            ({"kind": "upload", "ref": "e1", "files": [2]}, "out of range"),
            ({"kind": "wait"}, "'text', 'text_gone' or 'seconds' above 0; set one"),
            ({"kind": "wait", "seconds": -1}, "between 0 and 10"),
            ({"kind": "wait", "seconds": WAIT_MAX_S + 1}, "between 0 and 10"),
            ({"kind": "dialog", "accept": "maybe"}, "true or false"),
        ],
    )
    def test_errors_name_index_and_field(self, item, message):
        with pytest.raises(ActionSpecError, match=message):
            parse_actions([item], files=["/a"])

    def test_error_names_the_failing_index(self):
        with pytest.raises(ActionSpecError, match=r"actions\[2\] \(hover\)"):
            parse_actions([CLICK, CLICK, {"kind": "hover"}])

    def test_secret_names_are_checked_when_known(self):
        with pytest.raises(
            ActionSpecError, match="unknown secret 'pw'; known secrets: bank"
        ):
            parse_actions(
                [{"kind": "type", "ref": "e1", "secret": "pw"}], secrets=["bank"]
            )
        with pytest.raises(ActionSpecError, match="none are configured"):
            parse_actions([{"kind": "type", "ref": "e1", "secret": "pw"}], secrets=[])
        parsed = parse_actions([{"kind": "type", "ref": "e1", "secret": "pw"}])
        assert parsed[0].secret == "pw" and parsed[0].text is None

    def test_values_are_normalised(self):
        parsed = parse_actions(
            [
                {"kind": "click", "x": "10.5", "y": 3, "modifiers": "Shift"},
                {"kind": "type", "ref": " e2 ", "text": "", "submit": "true"},
                {"kind": "select", "ref": "e3", "values": "Red"},
                {"kind": "scroll"},
                {"kind": "upload", "ref": "e4", "files": 0},
                {"kind": "check", "ref": "e5"},
                {"kind": "dialog"},
            ],
            files=["/a"],
        )
        assert (parsed[0].x, parsed[0].modifiers) == (10.5, ("Shift",))
        assert (parsed[1].ref, parsed[1].text, parsed[1].submit) == ("e2", "", True)
        assert parsed[2].values == ("Red",)
        assert (parsed[3].dx, parsed[3].dy) == (0.0, 600.0)
        assert parsed[4].files == (0,)
        assert parsed[5].checked is True
        assert parsed[6].accept is True

    @pytest.mark.parametrize(
        "item,label",
        [
            ({"kind": "click", "ref": "e1"}, "click e1"),
            ({"kind": "click", "x": 3, "y": 4.5}, "click at (3, 4.5)"),
            ({"kind": "type", "ref": "e1", "text": "secret words"}, "type e1"),
            ({"kind": "press", "key": "Enter"}, "press Enter"),
            ({"kind": "press", "key": "a", "ref": "e2"}, "press a on e2"),
            ({"kind": "drag", "ref": "e1", "to": "e2"}, "drag e1 to e2"),
            ({"kind": "wait", "seconds": 2}, "wait 2 s"),
            ({"kind": "wait", "text": "x"}, "wait for text"),
            ({"kind": "wait", "text_gone": "x"}, "wait for text gone"),
            ({"kind": "dialog", "accept": False}, "dialog dismiss"),
            ({"kind": "scroll", "dy": -100}, "scroll by (0, -100)"),
            ({"kind": "scroll", "ref": "e3"}, "scroll e3"),
            ({"kind": "hover", "ref": "e3"}, "hover e3"),
            ({"kind": "hover", "x": 5, "y": 6}, "hover at (5, 6)"),
            ({"kind": "drag", "x": 1, "y": 2, "to": "e4"}, "drag (1, 2) to e4"),
            ({"kind": "drag", "ref": "e1", "to_x": 3, "to_y": 4}, "drag e1 to (3, 4)"),
        ],
    )
    def test_labels_never_show_typed_text(self, item, label):
        assert parse_actions([item])[0].label == label


class FakePage:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def primitive(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return ["x"] if name == "select" else None

        return primitive


class FakeContext:
    """A minimal ActionContext recording what the batch asked of it."""

    def __init__(self, files=()):
        self.page = FakePage()
        self.timeout = 1.0
        self.files = list(files)
        self.events = []
        self.pending = False
        self.bad_refs = set()
        self.raise_on: dict[str, BaseException] = {}
        self.pause_error: BaseException | None = None
        self.timeouts: list[float] = []
        self.blocked_after: dict[int, str] = {}
        self.done = 0

    def require_no_dialog(self):
        if self.pending:
            raise DialogPendingError("a dialog is waiting")

    async def secret(self, name, ref):
        self.events.append(("secret", name, ref))
        return "s3cret-value"

    async def check_ref(self, ref):
        self.events.append(("check", ref))
        if ref in self.bad_refs:
            raise ActionFailedError(f"ref {ref} is stale")

    async def call(self, operation, timeout):
        self.timeouts.append(timeout)
        result = await operation()
        name = self.page.calls[-1][0]
        if name in self.raise_on:
            raise self.raise_on[name]
        return result

    async def settle(self):
        self.events.append(("settle",))

    async def answer_dialog(self, accept, text):
        self.events.append(("answer", accept, text))
        return "answered"

    def action_done(self):
        self.done += 1
        self.events.append(("done",))

    def blocked_landing(self):
        return self.blocked_after.get(self.done)

    async def pause(self, seconds):
        self.events.append(("pause", seconds))
        if self.pause_error is not None:
            raise self.pause_error


async def run(items, ctx=None, files=(), deadline_s=None):
    ctx = ctx or FakeContext(files)
    report = await run_batch(parse_actions(items, files=files), ctx, deadline_s)
    return report, ctx


class TestRunBatch:
    async def test_runs_in_order_and_settles_after_each(self):
        report, ctx = await run([CLICK, {"kind": "hover", "ref": "e6"}])
        assert [o.status for o in report.outcomes] == [DONE, DONE] and report.ok
        assert ctx.events == [
            ("check", "e5"),
            ("done",),
            ("settle",),
            ("check", "e6"),
            ("done",),
            ("settle",),
        ]

    async def test_stops_at_first_failure_and_skips_the_rest(self):
        ctx = FakeContext()
        ctx.bad_refs.add("e6")
        report, _ = await run(
            [CLICK, {"kind": "hover", "ref": "e6"}, {"kind": "press", "key": "a"}], ctx
        )
        assert [o.status for o in report.outcomes] == [DONE, FAILED, SKIPPED]
        assert report.outcomes[1].reason == "ref e6 is stale"
        assert report.stopped.action.index == 1 and not report.ok
        assert [c[0] for c in ctx.page.calls] == ["click"]

    async def test_dialog_opened_marks_pending_and_stops(self):
        ctx = FakeContext()
        ctx.raise_on["click"] = DialogOpenedError(None, "a confirm opened")
        report, _ = await run([CLICK, CLICK], ctx)
        assert [o.status for o in report.outcomes] == [PENDING, SKIPPED]
        assert report.outcomes[0].reason == "a confirm opened"
        assert report.outcomes[0].detail == ""

    async def test_pending_dialog_blocks_other_kinds_but_not_dialog(self):
        ctx = FakeContext()
        ctx.pending = True
        report, _ = await run([{"kind": "dialog", "text": "hi"}, CLICK], ctx)
        assert report.outcomes[0].status == DONE
        assert ctx.events[0] == ("answer", True, "hi")
        assert report.outcomes[1].status == FAILED
        assert "a dialog is waiting" in report.outcomes[1].reason

    @pytest.mark.parametrize(
        "error,lost,reason",
        [
            (DriverDisconnectedError("x"), True, "the browser closed unexpectedly"),
            (PageClosedError("x"), False, "the tab was closed"),
        ],
    )
    async def test_browser_or_tab_loss(self, error, lost, reason):
        ctx = FakeContext()
        ctx.raise_on["click"] = error
        report, _ = await run([CLICK, CLICK], ctx)
        assert report.lost is lost
        assert report.outcomes[0].reason == reason
        assert report.outcomes[1].status == SKIPPED

    async def test_reasons_hide_typed_text(self):
        ctx = FakeContext()
        ctx.raise_on["fill"] = DriverError("cannot fill 'my private note'")
        report, _ = await run(
            [{"kind": "type", "ref": "e1", "text": "my private note"}], ctx
        )
        assert report.outcomes[0].reason == "cannot fill '[typed text]'"

    async def test_wait_seconds_pauses_and_skips_settle(self):
        report, ctx = await run([{"kind": "wait", "seconds": 0.01}])
        assert report.ok and ("settle",) not in ctx.events
        assert ("pause", 0.01) in ctx.events

    async def test_dialog_during_a_pause_is_pending(self):
        ctx = FakeContext()
        ctx.pause_error = DialogOpenedError(None, "a confirm dialog opened")
        report, _ = await run([{"kind": "wait", "seconds": 5}, CLICK], ctx)
        assert [o.status for o in report.outcomes] == [PENDING, SKIPPED]

    async def test_page_detail_is_kept_apart_from_the_reason(self):
        ctx = FakeContext()
        ctx.raise_on["fill"] = DriverError(
            "the call failed", detail="page says 'my private note'"
        )
        report, _ = await run(
            [{"kind": "type", "ref": "e1", "text": "my private note"}], ctx
        )
        outcome = report.outcomes[0]
        assert outcome.reason == "the call failed"
        assert outcome.detail == "page says '[typed text]'"
        assert format_details(report) == "Action 1: page says '[typed text]'"
        assert "page says" not in format_report(report)

    async def test_typing_slowly_gives_the_driver_the_longer_timeout(self):
        report, ctx = await run(
            [{"kind": "type", "ref": "e1", "text": "abcd", "slowly": True}]
        )
        assert report.ok
        typed = [c for c in ctx.page.calls if c[0] == "type_text"]
        assert typed == [("type_text", ("e1", "abcd", 1.2), {})]
        assert ctx.timeouts[-1] == pytest.approx(1.2)

    async def test_hover_and_drag_by_coordinates(self):
        report, ctx = await run(
            [
                {"kind": "hover", "x": 10, "y": 20},
                {"kind": "drag", "x": 1, "y": 2, "to_x": 3, "to_y": 4},
                {"kind": "drag", "ref": "e1", "to_x": 5, "to_y": 6},
                {"kind": "drag", "ref": "e1", "to": "e2"},
            ]
        )
        assert report.ok
        assert [c for c in ctx.page.calls] == [
            ("hover_at", (10.0, 20.0, 1.0), {}),
            ("drag_between", ((1.0, 2.0), (3.0, 4.0), 1.0), {}),
            ("drag_between", ("e1", (5.0, 6.0), 1.0), {}),
            ("drag", ("e1", "e2", 1.0), {}),
        ]
        assert ("check", "e1") in ctx.events and ("check", "e2") in ctx.events

    async def test_secret_is_resolved_at_execution(self):
        report, ctx = await run([{"kind": "type", "ref": "e1", "secret": "pw"}])
        assert ctx.page.calls[0] == ("fill", ("e1", "s3cret-value", 1.0), {})
        assert ctx.events[:2] == [("check", "e1"), ("secret", "pw", "e1")]
        assert report.outcomes[0].reason == "typed secret pw"

    async def test_a_refused_secret_types_nothing(self):
        ctx = FakeContext()

        async def refuse(name, ref):
            raise ActionFailedError(f"refused: secret {name} may not go to {ref}")

        ctx.secret = refuse
        report = await run_batch(
            parse_actions([{"kind": "type", "ref": "e1", "secret": "pw"}]), ctx
        )
        assert ctx.page.calls == []
        assert report.outcomes[0].status == "failed"
        assert report.outcomes[0].reason == "refused: secret pw may not go to e1"

    async def test_cancellation_propagates(self):
        ctx = FakeContext()

        async def never():
            await asyncio.Event().wait()

        ctx.page.__dict__["click"] = lambda *a, **k: never()
        task = asyncio.create_task(run_batch(parse_actions([CLICK]), ctx))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_a_blocked_landing_fails_that_action_and_stops(self):
        ctx = FakeContext()
        ctx.blocked_after[2] = "it led to c.test, which the policy blocked"
        report, _ = await run([CLICK, {"kind": "wait", "seconds": 0.01}, CLICK], ctx)
        assert [o.status for o in report.outcomes] == [DONE, FAILED, SKIPPED]
        assert report.outcomes[1].reason == "it led to c.test, which the policy blocked"
        assert report.stopped.action.index == 1
        assert [c[0] for c in ctx.page.calls] == ["click"]

    async def test_the_batch_deadline_fails_the_running_action(self):
        ctx = FakeContext()
        started = []

        async def hang(*args, **kwargs):
            started.append(args)
            await asyncio.Event().wait()

        ctx.page.__dict__["hover"] = hang
        report, _ = await run(
            [CLICK, {"kind": "hover", "ref": "e6"}, CLICK], ctx, deadline_s=0.05
        )
        assert [o.status for o in report.outcomes] == [DONE, FAILED, SKIPPED]
        assert report.outcomes[1].reason == (
            "the batch reached its 0.05 s time limit while this action ran"
        )
        assert report.stopped.action.index == 1
        assert len(started) == 1
        assert [c[0] for c in ctx.page.calls] == ["click"]

    async def test_an_action_due_after_the_deadline_is_not_started(self):
        ctx = FakeContext()

        async def slow_settle():
            await asyncio.sleep(0.1)

        ctx.settle = slow_settle
        report, _ = await run([CLICK, CLICK, CLICK], ctx, deadline_s=0.05)
        assert [o.status for o in report.outcomes] == [DONE, FAILED, SKIPPED]
        assert report.outcomes[1].reason == (
            "not started: the batch reached its 0.05 s time limit"
        )
        assert [c[0] for c in ctx.page.calls] == ["click"]

    def test_the_default_deadline_leaves_room_for_slow_actions(self):
        assert batch_deadline_s(5.0) == BATCH_MIN_S
        assert batch_deadline_s(100.0) == BATCH_SLOW_ACTIONS * (100.0 + WAIT_MAX_S)


class TestFormatReport:
    def test_lines(self):
        actions = parse_actions(
            [CLICK, {"kind": "type", "ref": "e2", "text": "abc"}, CLICK]
        )
        report = BatchReport(
            [
                Outcome(actions[0], DONE),
                Outcome(actions[1], FAILED, "ref e2 is stale"),
                Outcome(actions[2], SKIPPED),
            ]
        )
        assert format_report(report) == (
            "Actions: 1 of 3 done; stopped at 2 (failed)\n"
            "1. click e5: done\n"
            "2. type e2: failed — ref e2 is stale\n"
            "3. click e5: not executed"
        )

    def test_all_done(self):
        report = BatchReport([Outcome(parse_actions([CLICK])[0], DONE, "")])
        assert format_report(report) == "Actions: 1 of 1 done\n1. click e5: done"
