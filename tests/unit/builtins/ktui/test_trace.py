"""Sub-agent traces: matching tool ends, interrupts, cancels and how many are kept."""

from kohakuterrarium.builtins.ktui.trace import KEEP_FINISHED, Traces, trace_view


class TestTraces:
    def test_a_tool_end_closes_the_oldest_running_call_of_that_name(self):
        traces = Traces()
        traces.start("a", "explore", "find it", background=False)
        traces.tool("a", "start", "grep", "first")
        traces.tool("a", "start", "grep", "second")
        traces.tool("a", "done", "grep", "3 matches")
        steps = traces.get("a").steps
        assert [(s.detail, s.status) for s in steps] == [
            ("first", "ok"),
            ("second", "running"),
        ]
        assert steps[0].summary == "3 matches"
        # an end with no running call of that name changes nothing
        traces.tool("a", "error", "read", "boom")
        assert [s.status for s in steps] == ["ok", "running"]

    def test_an_interrupt_stops_foreground_sub_agents_only(self):
        traces = Traces()
        traces.start("fg", "explore", "", background=False)
        traces.start("bg", "worker", "", background=True)
        traces.tool("fg", "start", "grep", "")
        traces.interrupted()
        assert traces.get("fg").status == "interrupted"
        assert traces.get("fg").steps[0].status == "interrupted"
        assert traces.get("bg").running
        # a late end for the stopped one does not revive it
        traces.end("fg", result="late")
        assert traces.get("fg").status == "interrupted"
        assert traces.get("fg").result == ""

    def test_a_promoted_sub_agent_survives_the_interrupt_and_keeps_its_result(self):
        traces = Traces()
        traces.start("a", "explore", "", background=False)
        traces.promoted("a")
        traces.interrupted()
        assert traces.get("a").running
        traces.end("a", result="found it")
        assert (traces.get("a").status, traces.get("a").result) == ("ok", "found it")

    def test_a_failing_listener_does_not_stop_the_others(self):
        traces = Traces()
        heard = []

        def broken(_trace):
            raise RuntimeError("dialog gone")

        traces.listen(broken)
        traces.listen(lambda t: heard.append(t.status))
        traces.start("a", "explore", "", background=False)
        assert heard == ["running"]

    def test_listeners_hear_changes_until_they_stop(self):
        traces = Traces()
        heard = []
        stop = traces.listen(lambda t: heard.append((t.job_id, t.status)))
        traces.start("a", "explore", "", background=False)
        traces.end("a", error="failed")
        stop()
        traces.start("b", "explore", "", background=False)
        assert heard == [("a", "running"), ("a", "error")]

    def test_finished_traces_are_capped_running_ones_are_kept(self):
        traces = Traces()
        traces.start("live", "explore", "", background=True)
        for i in range(KEEP_FINISHED + 5):
            traces.start(f"t{i}", "explore", "", background=False)
            traces.end(f"t{i}", result="ok")
        finished = [t for t in traces.items.values() if not t.running]
        assert len(finished) == KEEP_FINISHED
        assert finished[0].job_id == "t5", "the oldest finished go first"
        assert traces.get("live") is not None

    def test_the_view_offers_stop_only_while_it_runs(self):
        traces = Traces()
        traces.start("a", "explore", "find it", background=False)
        running = trace_view(traces.get("a"), "solo", 10, 3, grouped=True)
        assert running.keys == {"e": "list calls", "s": "stop"}
        assert running.dock == "input"
        traces.cancelled("a")
        done = trace_view(traces.get("a"), "solo", 10, 3, grouped=False)
        assert done.keys == {"e": "group calls"}
        assert traces.get("a").stats == "cancelled by user"
