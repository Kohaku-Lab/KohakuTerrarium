"""ktui workflow: a real engine-hosted agent driven through kohakutui's emulated terminal.

One user journey per test: keys go in through ``App.inject`` exactly as a terminal would send
them, and assertions read what the user would see (scrollback plus screen). The agent is a
real ``Agent`` on a ``ScriptedLLM`` inside a real ``Terrarium``.
"""

import asyncio
import time
from collections.abc import Callable

import pytest
from kohakutui import App

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.builtins.kt_ui.markup import convert_block
from kohakuterrarium.builtins.ktui import KtuiConfig, KtuiShell
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config.types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)
from kohakuterrarium.terrarium.creature.host import Creature
from kohakuterrarium.terrarium.engine.engine import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


class _EchoTool(BaseTool):
    """DIRECT tool returning its bracket body."""

    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the call body back."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output=f"echoed:{args.get('content', '')}")


class _GateTool(BaseTool):
    """DIRECT tool that holds the turn open until the test opens the gate."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()

    @property
    def tool_name(self) -> str:
        return "gate"

    @property
    def description(self) -> str:
        return "Wait for the gate."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        await self.gate.wait()
        return ToolResult(output="gate opened")


def _creature(monkeypatch, tmp_path, script, name: str = "solo") -> Creature:
    def _fake_create(config, llm=None):
        return ScriptedLLM(script)

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)
    cfg = AgentConfig(
        name=name,
        llm_profile="openai/gpt-4-test",
        model="gpt-4",
        provider="openai",
        api_key_env="",
        system_prompt="You are a test agent.",
        include_tools_in_prompt=True,
        include_hints_in_prompt=False,
        tool_format="bracket",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="stdout"),
    )
    agent = Agent(cfg)
    return Creature(creature_id=name, name=name, agent=agent)


async def _until(
    predicate: Callable[[], bool], what: str, budget: float = 20.0
) -> None:
    deadline = time.monotonic() + budget
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def _seen(app: App) -> str:
    return "\n".join(app.history())


async def _type(app: App, text: str, key: str = "enter") -> None:
    app.inject({"text": text}, {"key": key})
    # the composer takes typed text at human pace; wait until it was submitted
    await asyncio.sleep(0.05 + 0.012 * len(text))


class TestKtuiWorkflow:
    @pytest.mark.timeout(120)
    async def test_chat_queue_approval_commands_and_exit(self, monkeypatch, tmp_path):
        """Chat with a tool, queue while busy, hide and answer an approval, run commands."""
        script = [
            ScriptEntry("[/echo]ping[echo/]", match="hello"),
            ScriptEntry("direct done", match="echoed:ping"),
            ScriptEntry(
                'Here is the plan.\n\n<ui title="Plan cost">\n'
                '<state seats={8} plan="team"/>\n'
                '<let price={plan == "team" ? 29 : 49}/>\n'
                '<stat label="Monthly">${fmt(seats * price)}</stat>\n'
                '<steps items={["Pick", "Ship"]} current={1}/>\n'
                "</ui>\n\nThat's all.",
                match="ui please",
            ),
            ScriptEntry(
                'Pick a size.\n\n<ui title="Plan">\n<state seats={8}/>\n'
                "<let total={seats * 29}/>\n"
                '<slider label="Seats" bind="seats" min={1} max={50}/>\n'
                '<stat label="Monthly">${fmt(total)}</stat>\n'
                '<button send="Go with {seats} seats">Choose</button>\n</ui>',
                match="form please",
            ),
            ScriptEntry("ten it is", match="Go with 10 seats"),
            ScriptEntry(
                "[/echo]a[echo/]\n[/echo]b[echo/]\n[/echo]c[echo/]\n[/echo]d[echo/]",
                match="four calls",
            ),
            ScriptEntry("batch done", match="echoed:d"),
            ScriptEntry("[/gate][gate/]", match="slow please"),
            ScriptEntry("slow finished", match="gate opened"),
            ScriptEntry("[/gate][gate/]", match="hold on"),
            # the folded message's call ends on the tool result, so it is the fallback
            ScriptEntry("queued reply"),
        ]
        creature = _creature(monkeypatch, tmp_path, script)
        gate = _GateTool()
        for tool in (_EchoTool(), gate):
            creature.agent.registry.register_tool(tool)
            creature.agent.executor.register_tool(tool)

        app = App(backend="test", size=(100, 30), interrupt="event")
        config = KtuiConfig(renderer="inline")
        async with Terrarium() as engine:
            await engine.add_creature(creature)
            shell = KtuiShell(engine, "solo", config, app=app)
            shell.attach(creature)
            run = asyncio.create_task(shell.run())
            await _until(lambda: shell.composer is not None, "the composer")

            # a turn with a direct tool: user message, folded ✓ tool cell, the answer
            await _type(app, "hello there")
            await _until(lambda: "direct done" in _seen(app), "the first answer")
            seen = _seen(app)
            assert "hello there" in seen
            assert "✓ echo" in seen and "echoed:ping" in seen, seen

            # ktui reports kt.ui, so the model is taught <ui>, and a block in a reply is drawn
            await _type(app, "ui please")
            await _until(lambda: "That's all." in _seen(app), "the reply with a block")
            seen = _seen(app)
            prompt = creature.agent.llm.last_messages[0]["content"]
            assert "### Inline components" in prompt, "the ui.declarative prompt"
            assert "Plan cost" in seen and "Monthly  $232" in seen, seen
            assert "✓ Pick" in seen and "● Ship" in seen, seen
            assert (
                "<ui" not in seen and "<stat" not in seen
            ), "no markup reaches the screen"

            # an interactive block: tab reaches its slider, the let value follows, the button
            # sends a message, the state is saved for the reply's turn, the next turn freezes it
            await _type(app, "form please")
            await _until(
                lambda: "tab to use" in "\n".join(app.screen()), "the live form"
            )
            app.inject({"key": "tab"}, {"key": "right"}, {"key": "right"})
            await _until(
                lambda: "Monthly  $290" in "\n".join(app.screen()), "the updated total"
            )
            app.inject({"key": "down"}, {"key": "enter"})
            await _until(
                lambda: "ten it is" in _seen(app), "the button's message answered"
            )
            assert "› Go with 10 seats" in _seen(app)
            saved = creature.agent.output_router.inline_ui._states
            await _until(lambda: saved, "the saved block state")
            assert list(saved.values()) == [{"seats": 10}], saved
            assert "tab to use" not in "\n".join(
                app.screen()
            ), "frozen by the next turn"

            # a kt.ui widget a tool opens: drawn by the same mapper, its state goes to the
            # widget service and its button sends a named widget message, never a user turn
            spec = convert_block(
                '<ui>\n<state n={3}/>\n<slider label="Workers" bind="n" min={1} max={8}/>\n'
                "<text>will start {n} workers</text>\n</ui>"
            )
            spec["root"]["children"].append(
                {
                    "type": "button",
                    "label": "Start",
                    "send": "start",
                    "data": {"n": "{{ n }}"},
                }
            )
            turns = _seen(app).count("› ")
            widgets = creature.agent.output_router.widgets
            handle = await widgets.open("kt.ui", spec, title="Deploy", routing="drop")
            await _until(
                lambda: "will start 3 workers" in "\n".join(app.screen()), "widget"
            )
            waiter = asyncio.create_task(handle.wait({"start"}, timeout_s=10))
            app.inject({"key": "tab"}, {"key": "right"}, {"key": "right"})
            # same-length text: the redraw must not come from a stale render cache
            await _until(
                lambda: "will start 5 workers" in "\n".join(app.screen()), "the redraw"
            )
            assert handle.state == {"n": 5}, handle.state
            app.inject({"key": "down"}, {"key": "enter"})
            msg = await waiter
            assert msg is not None and (msg.name, msg.data) == ("start", {"n": 5})
            await handle.update(state={"n": 7}, title="Deploy now")
            await _until(
                lambda: "will start 7 workers" in "\n".join(app.screen()), "tool update"
            )
            assert "Deploy now" in "\n".join(app.screen())
            await handle.close()
            await _until(
                lambda: "tab to use" not in "\n".join(app.screen()), "frozen on close"
            )
            assert _seen(app).count("› ") == turns, "the widget started no turn"
            assert not shell.tool_widgets.open

            # four calls in a row fold into one web-style batch line, sealed into scrollback
            await _type(app, "four calls please")
            await _until(
                lambda: "batch done" in _seen(app), "the batched turn's answer"
            )
            seen = _seen(app)
            assert "✓ 4 tool calls  echo ×4  ▸" in seen, seen
            assert "echoed:a" not in seen, "folded: the calls are inside the batch"

            # Enter while the agent works queues the message; KT folds it into the running
            # turn (moving the blocking tool to the background), so it lands as a user message
            await _type(app, "slow please")
            await _until(
                lambda: "gate" in "\n".join(app.screen()), "the running gate tool"
            )
            await _type(app, "and then this")
            await _until(
                lambda: "queued reply" in _seen(app), "the queued message's answer"
            )
            assert "and then this" in _seen(app)
            assert "queued · and then this" not in "\n".join(app.screen())
            gate.gate.set()
            await _until(
                lambda: "slow finished" in _seen(app), "the backgrounded tool's result"
            )
            seen = _seen(app)
            assert "gate  background" in seen, "a promoted call closes its cell at once"
            assert "background result delivered · gate\n" in seen + "\n"
            assert "gate[" not in seen

            # esc on the prompt (no dialog open) interrupts the running turn
            gate.gate = asyncio.Event()
            await _type(app, "hold on")
            await _until(lambda: shell.focused.view.jobs, "the second gate call")
            app.inject({"key": "esc"})
            await _until(lambda: "○ interrupted" in _seen(app), "the interrupt")
            await _until(lambda: not shell.is_busy(shell.focused), "the idle prompt")

            # an approval: esc hides it without answering, alt+j brings it back, 1 answers
            router = creature.agent.output_router
            event = OutputEvent(
                type="confirm",
                interactive=True,
                id="ask-1",
                payload={
                    "prompt": "Allow tool: bash?",
                    "detail": "rm -rf build",
                    "options": [
                        {"id": "allow_once", "label": "Allow once", "style": "primary"},
                        {"id": "deny", "label": "Deny", "style": "danger"},
                    ],
                },
            )
            answer = asyncio.create_task(router.emit_and_wait(event, timeout_s=60))
            await _until(
                lambda: "Allow tool: bash?" in "\n".join(app.screen()),
                "the approval card",
            )
            # docked: it sits where the input box was
            await asyncio.sleep(0.1)
            assert not any("message · / commands" in line for line in app.screen())
            app.inject({"key": "esc"})
            await _until(
                lambda: "Allow tool: bash?" not in "\n".join(app.screen()),
                "the card hidden",
            )
            await _until(
                lambda: "! 1 waiting" in "\n".join(app.screen()), "the waiting item"
            )
            assert any("message · / commands" in line for line in app.screen())
            assert not answer.done()
            # ↓ to the active row, enter on its "waiting" segment: the approval comes back
            app.inject({"key": "down"}, {"key": "enter"})
            await _until(
                lambda: "Allow tool: bash?" in "\n".join(app.screen()),
                "the card back from the row",
            )
            app.inject({"key": "esc"})
            await _until(
                lambda: "Allow tool: bash?" not in "\n".join(app.screen()),
                "hidden again",
            )
            app.inject({"key": "alt+j"})
            await _until(
                lambda: "Allow tool: bash?" in "\n".join(app.screen()), "the card back"
            )
            app.inject({"text": "1"})
            reply = await asyncio.wait_for(answer, 10)
            assert reply.action_id == "allow_once"
            await _until(
                lambda: "! 1 waiting" not in "\n".join(app.screen()), "the row cleared"
            )

            # answered by another client (the web UI): the card and the waiting item go away
            other = OutputEvent(
                type="confirm",
                interactive=True,
                id="ask-2",
                payload={"prompt": "Allow tool: write?", "options": []},
            )
            waiting = asyncio.create_task(router.emit_and_wait(other, timeout_s=60))
            await _until(
                lambda: "Allow tool: write?" in "\n".join(app.screen()),
                "the second card",
            )
            router.submit_reply(UIReply(event_id="ask-2", action_id="confirm"))
            assert (await asyncio.wait_for(waiting, 10)).action_id == "confirm"
            await _until(
                lambda: "Allow tool: write? · answered elsewhere" in _seen(app),
                "the card closed by the other client",
            )
            # (the closing line reaches scrollback on the frame that removes the card, which
            # the screen copy may not have caught yet)
            await _until(
                lambda: not any(l.startswith("│ Allow tool") for l in app.screen()),
                "the card gone from the screen",
            )
            assert shell.interactions.count == 0

            # a multi-select question: the default is ticked, space ticks more, enter answers
            # every ticked id in option order (one id, not a list, for single choice)
            options = [{"id": c, "label": c.title()} for c in ("red", "green", "blue")]
            pick = OutputEvent(
                type="selection",
                interactive=True,
                id="ask-3",
                payload={
                    "prompt": "Pick colours",
                    "multi": True,
                    "options": options,
                    "default": ["green"],
                },
            )
            asked = asyncio.create_task(router.emit_and_wait(pick, timeout_s=60))
            await _until(
                lambda: "☑ Green" in "\n".join(app.screen()), "the ticked default"
            )
            app.inject(
                {"key": "down"}, {"key": "down"}, {"text": " "}, {"key": "enter"}
            )
            reply = await asyncio.wait_for(asked, 10)
            assert reply.values == {"selected": ["green", "blue"]}, reply
            single = OutputEvent(
                type="selection",
                interactive=True,
                id="ask-4",
                payload={"prompt": "Pick one", "options": options},
            )
            asked = asyncio.create_task(router.emit_and_wait(single, timeout_s=60))
            await _until(lambda: "Pick one" in "\n".join(app.screen()), "single choice")
            app.inject({"key": "down"}, {"key": "enter"})
            reply = await asyncio.wait_for(asked, 10)
            assert reply.values == {"selected": "green"}, reply

            # local and unknown commands
            await _type(app, "/theme tokyo-night")
            await _until(
                lambda: KtuiConfig.load().theme == "tokyo-night", "the saved theme"
            )
            await _type(app, "/definitely-not-a-command")
            await _until(
                lambda: "unknown command" in _seen(app), "the unknown-command error"
            )

            # bare /model opens the picker (not a printed list); esc leaves the model alone
            before = creature.agent.llm_identifier()
            await _type(app, "/model")
            await _until(
                lambda: any("Model · solo · now" in line for line in app.screen()),
                "the model picker",
            )
            # docked where the input box was, with a provider column; no mode switch
            await asyncio.sleep(0.2)
            screen = app.screen()
            assert not any(
                "message · / commands" in line for line in screen
            ), "input replaced"
            assert any(" │ " in line and "openai" in line for line in screen), screen
            assert shell.mode == "inline"
            app.inject({"key": "esc"})
            await _until(
                lambda: not any("Model · solo" in line for line in app.screen()),
                "the picker closed",
            )
            await _until(
                lambda: any("message · / commands" in line for line in app.screen()),
                "the input box back",
            )
            assert creature.agent.llm_identifier() == before

            # \ + enter is a newline, not a send
            app.inject({"text": "first line\\"}, {"key": "enter"}, {"text": "second"})
            await _until(
                lambda: "second" in "\n".join(app.screen()), "the second composer line"
            )
            assert "first line" in "\n".join(app.screen())
            assert "› first line" not in _seen(app), "nothing was sent yet"
            app.inject({"key": "enter"})
            await _until(
                lambda: "› first line\n  second" in _seen(app), "one two-line message"
            )
            await _until(lambda: not shell.is_busy(shell.focused), "the idle prompt")

            # a sub-agent's trace: listed on the board, opened docked, live, then kept
            out = shell.focused.output
            job = {"job_id": "sa-1"}
            out.on_activity_with_metadata(
                "subagent_start",
                "[explore] find the flaky test",
                {**job, "task": "find the flaky test", "background": True},
            )
            for tool in ("grep", "read", "grep"):
                md = {**job, "tool": tool, "detail": "3 matches"}
                out.on_activity_with_metadata(
                    "subagent_tool_start", f"[explore] {tool}", md
                )
                out.on_activity_with_metadata(
                    "subagent_tool_done", f"[explore] {tool}", md
                )
            app.inject({"key": "ctrl+b"})
            await _until(
                lambda: "⚙ explore  find the flaky test" in "\n".join(app.screen()),
                "the running sub-agent on the board",
            )
            app.inject({"key": "enter"})
            await _until(
                lambda: "sub-agent trace" in "\n".join(app.screen()), "the trace"
            )
            # the body and its key hint can land a frame apart: wait for the whole view
            await _until(
                lambda: "e list calls" in "\n".join(app.screen()), "the trace's keys"
            )
            screen = "\n".join(app.screen())
            assert "find the flaky test" in screen and "3 tool calls" in screen, screen
            assert "s stop" in screen
            app.inject({"text": "e"})
            await _until(
                lambda: "e group calls" in "\n".join(app.screen()), "the calls listed"
            )
            out.on_activity_with_metadata(
                "subagent_done",
                "[explore] done",
                {**job, "result": "it is test_retry, a timing race"},
            )
            await _until(
                lambda: "timing race" in "\n".join(app.screen()),
                "the open trace following the result",
            )
            assert "s stop" not in "\n".join(app.screen()), "nothing left to stop"
            app.inject({"key": "esc"})
            await _until(
                lambda: any("message · / commands" in line for line in app.screen()),
                "the input box back after the trace",
            )
            await _type(app, "/agents")
            await _until(
                lambda: "✓ explore  find the flaky test" in "\n".join(app.screen()),
                "the finished sub-agent kept on the board",
            )
            app.inject({"key": "esc"})
            await _until(
                lambda: any("message · / commands" in line for line in app.screen()),
                "the input box back after the board",
            )

            # /config: one categorised picker; focus the options, change the value, apply
            await _type(app, "/config")
            await _until(
                lambda: any("Settings" in line for line in app.screen()), "/config"
            )
            screen = "\n".join(app.screen())
            for section in ("display", "models", "keys"):
                assert section in screen, screen
            app.inject({"text": "tool calls"})
            await asyncio.sleep(0.3)
            # → focuses the setting's options, ↓ picks the next value, enter applies
            app.inject({"key": "right"}, {"key": "down"}, {"key": "enter"})
            await _until(
                lambda: KtuiConfig.load().verbosity == "high", "the saved verbosity"
            )

            await _type(app, "/exit")
            await asyncio.wait_for(run, 10)
            assert (
                creature.agent.output_router.default_output is not shell.focused.output
            )

    @pytest.mark.timeout(120)
    async def test_second_creature_buffers_until_focused(self, monkeypatch, tmp_path):
        """``@name`` talks to another creature; its reply waits off-screen until /focus.
        Then full screen: rail, tabs, a channel tab that sends, aux tabs, sizes saved.
        """
        saved = tmp_path / "ktui.json"
        monkeypatch.setattr(KtuiConfig, "default_path", staticmethod(lambda: saved))
        root = _creature(monkeypatch, tmp_path, ["root here"], "root")
        helper = _creature(
            monkeypatch, tmp_path, [ScriptEntry("helper reply", match="hi")], "helper"
        )
        app = App(backend="test", size=(100, 30), interrupt="event")
        async with Terrarium() as engine:
            await engine.add_creature(root)
            await engine.add_creature(helper)
            shell = KtuiShell(engine, "root", KtuiConfig(renderer="inline"), app=app)
            shell.attach(root)
            shell.attach(helper)
            run = asyncio.create_task(shell.run())
            await _until(lambda: shell.composer is not None, "the composer")

            await _type(app, "@helper hi")
            await _until(
                lambda: helper.agent.controller.conversation.get_last_assistant_message()
                is not None,
                "helper's answer",
            )
            unread = shell.entries["helper"].view
            await _until(lambda: unread.unread > 0, "the unread count")
            assert "helper reply" not in _seen(app), "unfocused output stays off-screen"

            await _type(app, "/focus helper")
            await _until(lambda: "helper reply" in _seen(app), "the replayed backlog")
            seen = _seen(app)
            assert seen.index("── helper ──") < seen.index("helper reply")
            assert shell.focused.creature is helper
            assert unread.unread == 0

            # a creature that joins the graph while ktui runs is attached and can be talked to
            late = _creature(
                monkeypatch, tmp_path, [ScriptEntry("late reply", match="hey")], "late"
            )
            await engine.add_creature(late, graph=helper.graph_id)
            await _until(lambda: "late" in shell.entries, "the late creature attached")
            await _type(app, "@late hey")
            await _until(
                lambda: late.agent.controller.conversation.get_last_assistant_message()
                is not None,
                "late's answer",
            )

            # and one that leaves the engine is let go
            await engine.remove_creature(late)
            await _until(lambda: "late" not in shell.entries, "the late creature gone")

            # full screen: pages on top, the rail lists agents and the graph's channels
            await engine.add_channel(helper.graph_id, "review", "review requests")
            app.inject({"key": "ctrl+t"})
            await _until(lambda: "#review" in "\n".join(app.screen()), "the rail")
            rows = app.screen()
            assert "Chat" in rows[0] and "Status" in rows[0], "the page tabs"

            def click(word: str) -> None:
                rows = app.screen()
                y = next(i for i, line in enumerate(rows) if word in line[:30])
                app.inject({"click": (rows[y].index(word), y)})

            # a channel picked in the rail: its chat, and typing sends to the channel
            click("#review")
            await _until(lambda: "#review  ◂" in "\n".join(app.screen()), "the channel")
            await _type(app, "please look")
            channel = engine.channel(helper.graph_id, "review")
            await _until(
                lambda: any(m.content == "please look" for m in channel.history),
                "the message on the channel",
            )
            await _until(
                lambda: "please look" in "\n".join(app.screen()), "it in the chat"
            )
            # a page replaces the chat (and its panels); alt+1 is the chat again
            app.inject({"key": "alt+3"})
            await _until(
                lambda: any(line.startswith("Agents") for line in app.screen()),
                "the Status page",
            )
            app.inject({"key": "alt+1"})
            await _until(lambda: "#review  ◂" in "\n".join(app.screen()), "chat again")
            # the creature picked in the rail: its chat; the composer talks to it again
            click("helper")
            await _until(lambda: "helper reply" in "\n".join(app.screen()), "back")
            # the rail divider dragged: the width is remembered
            divider = app.screen()[1].index("│")
            app.inject({"drag": (divider, 4, divider + 6)})
            await _until(
                lambda: saved.exists() and '"rail"' in saved.read_text(), "saved panes"
            )
            assert KtuiConfig.load(saved).panes["rail"]["width"] == divider + 6

            await _type(app, "/exit")
            await asyncio.wait_for(run, 10)
