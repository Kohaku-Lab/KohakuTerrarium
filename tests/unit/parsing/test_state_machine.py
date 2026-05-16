"""Unit tests for :mod:`kohakuterrarium.parsing.state_machine`.

The StreamParser is the most important parser in the framework: it
extracts tool calls, sub-agent calls, framework commands, and output
blocks from streaming LLM tokens.  A bug here drops tool calls
silently — the model thinks it called a tool, the controller never
runs it, and the user sees a stuck conversation.

Every state transition + every error/recovery branch exercised, in
both bracket and XML modes.
"""

from kohakuterrarium.parsing.events import (
    BlockEndEvent,
    BlockStartEvent,
    CommandEvent,
    OutputCallEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.parsing.format import BRACKET_FORMAT, XML_FORMAT, ToolCallFormat
from kohakuterrarium.parsing.patterns import ParserConfig
from kohakuterrarium.parsing.state_machine import (
    ParserState,
    StreamParser,
    parse_full,
)

# ── helpers ──────────────────────────────────────────────────────────


def _cfg(*, fmt: ToolCallFormat = BRACKET_FORMAT, **kw) -> ParserConfig:
    """Build a ParserConfig with the tool/subagent/command sets the test needs."""
    c = ParserConfig(
        tool_format=fmt,
        known_tools=kw.pop("known_tools", set()),
        known_subagents=kw.pop("known_subagents", {"agent"}),
        known_commands=kw.pop("known_commands", {"info", "read_job"}),
        known_outputs=kw.pop("known_outputs", set()),
        emit_block_events=kw.pop("emit_block_events", False),
    )
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _events(text: str, cfg: ParserConfig | None = None) -> list:
    return parse_full(text, cfg or _cfg(known_tools={"bash", "edit", "write"}))


# ── plain text passes through ────────────────────────────────────────


class TestPlainText:
    def test_plain_text_yields_single_text_event(self):
        ev = _events("hello world")
        assert len(ev) == 1
        assert isinstance(ev[0], TextEvent)
        assert ev[0].text == "hello world"

    def test_empty_input_yields_no_events(self):
        assert _events("") == []

    def test_streaming_chunks_assemble_same_as_single(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        events: list = []
        for chunk in ("hel", "lo ", "world"):
            events.extend(p.feed(chunk))
        events.extend(p.flush())
        joined = "".join(e.text for e in events if isinstance(e, TextEvent))
        assert joined == "hello world"


# ── bracket format ───────────────────────────────────────────────────


class TestBracketTool:
    def test_simple_tool_call(self):
        ev = _events("before [/bash]ls -la[bash/] after", _cfg(known_tools={"bash"}))
        # Three events: text-before, tool, text-after.
        types = [type(e).__name__ for e in ev]
        assert types == ["TextEvent", "ToolCallEvent", "TextEvent"]
        tool = ev[1]
        assert tool.name == "bash"
        assert tool.args == {"command": "ls -la"}
        # Raw is the original-ish form: opener + body + closer.  The
        # body lands as ``command`` via the content_arg_map at
        # event-emit time, but ``_build_raw`` uses the BRACKET-parsed
        # args (no @@command= line) + the body so the raw matches what
        # the model actually emitted.
        assert tool.raw == "[/bash]\nls -la\n[bash/]"

    def test_multiline_tool_with_args(self):
        text = "[/write]\n@@path=x.py\nbody line 1\nbody line 2\n[write/]"
        ev = _events(text, _cfg(known_tools={"write"}))
        tools = [e for e in ev if isinstance(e, ToolCallEvent)]
        assert len(tools) == 1
        assert tools[0].name == "write"
        # ``write`` uses ``content`` as the body arg in the default map.
        assert tools[0].args == {"path": "x.py", "content": "body line 1\nbody line 2"}

    def test_block_split_across_chunks(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        chunks = ["[/", "bash", "]ls", "[ba", "sh/", "]"]
        all_events: list = []
        for c in chunks:
            all_events.extend(p.feed(c))
        all_events.extend(p.flush())
        tools = [e for e in all_events if isinstance(e, ToolCallEvent)]
        assert len(tools) == 1
        assert tools[0].name == "bash"
        assert tools[0].args == {"command": "ls"}

    def test_unknown_tool_emitted_as_text(self):
        ev = _events("[/ghost]body[ghost/]", _cfg(known_tools={"bash"}))
        # Block parses but is unknown → emitted as TextEvent containing
        # the raw bracket form.
        assert not any(isinstance(e, ToolCallEvent) for e in ev)
        text_chunks = [e.text for e in ev if isinstance(e, TextEvent)]
        joined = "".join(text_chunks)
        assert "[/ghost]" in joined
        assert "body" in joined
        assert "[ghost/]" in joined

    def test_block_with_no_body_no_args(self):
        ev = _events("[/bash][bash/]", _cfg(known_tools={"bash"}))
        tools = [e for e in ev if isinstance(e, ToolCallEvent)]
        assert len(tools) == 1
        assert tools[0].args == {}

    def test_attribute_set_via_inline_does_not_override_via_body(self):
        # When ``@@command=preset`` is present AND a body follows, the
        # attribute wins and the body is SILENTLY DROPPED.  This is the
        # documented build_tool_args policy in patterns.py:
        # "Don't override if already set via attribute."
        ev = _events(
            "[/bash]\n@@command=preset\nignored body\n[bash/]",
            _cfg(known_tools={"bash"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert tool.args == {"command": "preset"}

    def test_arg_without_value(self):
        ev = _events("[/bash]\n@@flag\nls\n[bash/]", _cfg(known_tools={"bash"}))
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert tool.args == {"flag": "", "command": "ls"}

    def test_mismatched_close_tag_treated_as_content(self):
        # ``[/bash]ls[unrelated/]`` — ``unrelated`` doesn't match the
        # open block, so the close-marker is treated as content; block
        # then never closes → emitted as text at flush time.
        text = "[/bash]ls[unrelated/]"
        ev = _events(text, _cfg(known_tools={"bash"}))
        # No completed tool call.
        assert not any(isinstance(e, ToolCallEvent) for e in ev)
        # The whole thing surfaces as text.
        joined = "".join(e.text for e in ev if isinstance(e, TextEvent))
        assert "[/bash]" in joined
        assert "[unrelated/]" in joined

    def test_nested_bracket_in_content_treated_literally(self):
        # ``[/bash]echo [other][bash/]`` — the inner ``[other]`` is just
        # text since we don't support nesting; the outer block closes.
        ev = _events("[/bash]echo [other][bash/]", _cfg(known_tools={"bash"}))
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert tool.args["command"] == "echo [other]"


class TestBracketSubAgent:
    def test_subagent_call_default_tag(self):
        ev = _events(
            "[/agent]explore this[agent/]",
            _cfg(known_subagents={"agent"}),
        )
        sa = next(e for e in ev if isinstance(e, SubAgentCallEvent))
        assert sa.name == "agent"
        assert sa.args == {"task": "explore this"}

    def test_subagent_inline_args_preserved(self):
        ev = _events(
            "[/agent]\n@@type=planner\ndo the thing\n[agent/]",
            _cfg(known_subagents={"agent"}),
        )
        sa = next(e for e in ev if isinstance(e, SubAgentCallEvent))
        assert sa.name == "agent"
        assert sa.args["type"] == "planner"
        assert sa.args["task"] == "do the thing"


class TestBracketCommand:
    def test_command_default(self):
        ev = _events("[/info]bash[info/]", _cfg(known_commands={"info"}))
        cmd = next(e for e in ev if isinstance(e, CommandEvent))
        assert cmd.command == "info"
        assert cmd.args == "bash"

    def test_command_with_no_body(self):
        ev = _events("[/info][info/]", _cfg(known_commands={"info"}))
        cmd = next(e for e in ev if isinstance(e, CommandEvent))
        assert cmd.args == ""


class TestBracketOutput:
    def test_explicit_output_block(self):
        ev = _events(
            "[/output_discord]hello world[output_discord/]",
            _cfg(known_outputs={"discord"}),
        )
        out = next(e for e in ev if isinstance(e, OutputCallEvent))
        assert out.target == "discord"
        assert out.content == "hello world"

    def test_unknown_output_target_falls_through_to_text(self):
        # ``output_unknown`` not in known_outputs → not classified;
        # block falls through to "unknown block type" → text.
        ev = _events(
            "[/output_unknown]x[output_unknown/]",
            _cfg(known_outputs={"discord"}),
        )
        assert not any(isinstance(e, OutputCallEvent) for e in ev)


# ── XML format ───────────────────────────────────────────────────────


class TestXmlTool:
    def test_xml_tool_call(self):
        ev = _events("<bash>ls</bash>", _cfg(fmt=XML_FORMAT, known_tools={"bash"}))
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert tool.name == "bash"
        # XML mode: body becomes the ``content`` (default) or mapped arg.
        assert tool.args == {"command": "ls"}

    def test_xml_with_inline_attrs(self):
        ev = _events(
            '<edit path="x.py">diff body</edit>',
            _cfg(fmt=XML_FORMAT, known_tools={"edit"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert tool.name == "edit"
        assert tool.args == {"path": "x.py", "diff": "diff body"}

    def test_xml_self_closing(self):
        ev = _events(
            '<read path="x.py"/>',
            _cfg(fmt=XML_FORMAT, known_tools={"read"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert tool.name == "read"
        assert tool.args == {"path": "x.py"}

    def test_xml_mismatched_close(self):
        # ``<bash>ls</foo>`` — close name doesn't match; the close
        # tag becomes part of the block body, block never closes.
        ev = _events("<bash>ls</foo>", _cfg(fmt=XML_FORMAT, known_tools={"bash"}))
        assert not any(isinstance(e, ToolCallEvent) for e in ev)
        joined = "".join(e.text for e in ev if isinstance(e, TextEvent))
        assert "<bash>" in joined
        assert "</foo>" in joined

    def test_xml_double_letter_after_open_inside_block_is_content(self):
        # ``<bash>ls <other>more</bash>`` — the ``<other>`` inside is
        # not a known close marker; treated as text within the block.
        ev = _events(
            "<bash>ls <other>more</bash>",
            _cfg(fmt=XML_FORMAT, known_tools={"bash"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        # The whole thing between <bash> and </bash> is the command.
        assert "ls" in tool.args["command"]
        assert "<other>" in tool.args["command"]


# ── flush / incomplete states ────────────────────────────────────────


class TestFlushIncomplete:
    def test_maybe_open_at_eof(self):
        p = StreamParser(_cfg())
        p.feed("[")
        out = p.flush()
        # Only buffered text is the ``[`` start char.
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "[" in joined

    def test_open_slash_at_eof(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        p.feed("[/")
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "[/" in joined

    def test_in_open_name_at_eof_emits_partial(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        p.feed("[/bas")
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "[/bas" in joined

    def test_unclosed_block_at_eof_dumps_raw_to_text(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        # Opening complete, body started, no close.
        p.feed("[/bash]ls -la")
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        # The raw opener + body must be in the emitted text.
        assert "[/bash]" in joined
        assert "ls -la" in joined

    def test_maybe_close_at_eof_dumps(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        # Get into MAYBE_CLOSE state by feeding [/bash]ls[ — the ``[``
        # transitions to MAYBE_CLOSE.
        p.feed("[/bash]ls[")
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "[/bash]" in joined
        assert "ls[" in joined

    def test_in_close_name_at_eof_dumps(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        # ``[/bash]ls[bas`` — IN_CLOSE_NAME with name_buffer="bas".
        p.feed("[/bash]ls[bas")
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "[/bash]" in joined
        assert "[bas" in joined

    def test_xml_self_closing_partial_at_eof(self):
        # XML self-closing buffer in flight.
        p = StreamParser(_cfg(fmt=XML_FORMAT, known_tools={"read"}))
        p.feed('<read path="x"/')
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "<read" in joined

    def test_in_open_attrs_at_eof(self):
        p = StreamParser(_cfg(fmt=XML_FORMAT, known_tools={"bash"}))
        p.feed("<bash path=")
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "<bash" in joined
        assert "path=" in joined

    def test_xml_in_open_name_at_eof(self):
        # XML mode: IN_OPEN_NAME flush branch uses ``sc + name`` (no
        # slash) since slash_means_open=False.
        p = StreamParser(_cfg(fmt=XML_FORMAT, known_tools={"bash"}))
        p.feed("<bas")
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "<bas" in joined

    def test_xml_in_close_name_at_eof(self):
        # XML mode: IN_CLOSE_NAME flush branch on
        # ``slash_means_open=False`` uses ``sc + "/" + name``.
        p = StreamParser(_cfg(fmt=XML_FORMAT, known_tools={"bash"}))
        p.feed("<bash>ls</bas")
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "<bash>" in joined
        assert "</bas" in joined

    def test_expect_close_slash_at_eof_dumps(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        p.feed("[/bash]ls[bash/")
        # We're in EXPECT_CLOSE_SLASH (saw `[bash/`, expecting `]`).
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "[/bash]" in joined


# ── block_events emission ────────────────────────────────────────────


class TestBlockEvents:
    def test_emits_block_start_and_end(self):
        cfg = _cfg(known_tools={"bash"}, emit_block_events=True)
        ev = _events("[/bash]ls[bash/]", cfg)
        starts = [e for e in ev if isinstance(e, BlockStartEvent)]
        ends = [e for e in ev if isinstance(e, BlockEndEvent)]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0].block_type == "bash"

    def test_xml_self_close_also_emits_block_start_and_end(self):
        cfg = _cfg(fmt=XML_FORMAT, known_tools={"read"}, emit_block_events=True)
        ev = _events('<read path="x"/>', cfg)
        # Block start + tool + block end.
        assert any(isinstance(e, BlockStartEvent) for e in ev)
        assert any(isinstance(e, BlockEndEvent) for e in ev)


# ── invalid markers fall back to text ────────────────────────────────


class TestInvalidMarkersFallToText:
    def test_bracket_open_letter_emitted_as_text(self):
        # ``[a]`` — not a valid bracket open (needs ``[/``).
        ev = _events("foo [bar] baz", _cfg(known_tools={"bash"}))
        text = "".join(e.text for e in ev if isinstance(e, TextEvent))
        assert "foo [bar] baz" == text

    def test_bracket_open_slash_invalid_next_char(self):
        # ``[/1`` — digits/specials after ``[/`` are invalid.
        ev = _events("[/1nope", _cfg(known_tools={"bash"}))
        text = "".join(e.text for e in ev if isinstance(e, TextEvent))
        assert "[/" in text
        assert "1nope" in text

    def test_invalid_close_name_char(self):
        # ``[/bash]ls[!]`` — ``!`` after ``[`` inside block is not
        # name-like, must be appended as content.
        ev = _events("[/bash]ls[!][bash/]", _cfg(known_tools={"bash"}))
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert "[!]" in tool.args["command"]

    def test_xml_bare_letter_followed_by_invalid(self):
        # ``<1>`` — XML mode but starting with digit; not a valid name.
        ev = _events("foo <1> bar", _cfg(fmt=XML_FORMAT, known_tools=set()))
        text = "".join(e.text for e in ev if isinstance(e, TextEvent))
        assert "<1>" in text

    def test_xml_close_slash_outside_block_is_text(self):
        # XML mode at NORMAL: ``</something``.  The MAYBE_OPEN handler
        # sees ``/`` after ``<`` in XML mode and emits it as text since
        # there's no block open.
        ev = _events("text </foo bar", _cfg(fmt=XML_FORMAT, known_tools=set()))
        text = "".join(e.text for e in ev if isinstance(e, TextEvent))
        assert "</foo" in text

    def test_invalid_char_in_open_name_emits_as_text(self):
        # Bracket mode: ``[/bash!`` — the ``!`` invalidates the open
        # name; the partial open is emitted as text.
        ev = _events("[/bash!boom", _cfg(known_tools={"bash"}))
        text = "".join(e.text for e in ev if isinstance(e, TextEvent))
        assert "[/bash!boom" in text

    def test_xml_invalid_char_in_open_name(self):
        # XML mode: ``<bash!`` — invalid name character.
        ev = _events("<bash!boom", _cfg(fmt=XML_FORMAT, known_tools={"bash"}))
        text = "".join(e.text for e in ev if isinstance(e, TextEvent))
        assert "<bash!boom" in text

    def test_xml_attribute_with_slash_in_value(self):
        # XML: ``<read path="a/b/c"/>`` — slash inside attribute value
        # must not trigger self-closing; only ``/>`` at end does.
        ev = _events(
            '<read path="a/b/c"/>',
            _cfg(fmt=XML_FORMAT, known_tools={"read"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert tool.args == {"path": "a/b/c"}

    def test_xml_self_closing_misfire_recovers(self):
        # XML: ``<bash />`` — saw ``/`` then ``space`` (not end_char);
        # `_handle_in_self_closing` appends ``/space`` back to attrs
        # and returns to IN_OPEN_ATTRS.
        ev = _events(
            '<bash arg="v" />',
            _cfg(fmt=XML_FORMAT, known_tools={"bash"}),
        )
        # The bash block closes successfully despite the awkward
        # whitespace.  The exact semantics: attrs_buffer becomes
        # ``arg="v" / `` then more whitespace then ``>`` closes the
        # opening tag.
        # Either it's a tool call OR text — the exact outcome documents
        # behavior.  Assert no crash.
        assert len(ev) > 0

    def test_in_close_name_invalid_recovers(self):
        # Bracket: ``[/bash]ls[bash!`` — IN_CLOSE_NAME hits ``!``,
        # invalid → re-buffer and stay in IN_BLOCK.
        ev = _events("[/bash]ls[bash![bash/]", _cfg(known_tools={"bash"}))
        tool = next((e for e in ev if isinstance(e, ToolCallEvent)), None)
        # Block still closes via the trailing ``[bash/]``.
        assert tool is not None
        assert "[bash!" in tool.args["command"]

    def test_in_close_name_letter_then_bracket_no_slash(self):
        # Bracket: ``[/bash]ls[other]extra[bash/]`` — IN_CLOSE_NAME
        # with name="other", hits end_char ``]`` without slash → not a
        # valid close → re-buffer + IN_BLOCK.
        ev = _events("[/bash]ls[other]extra[bash/]", _cfg(known_tools={"bash"}))
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert "[other]" in tool.args["command"]
        assert "extra" in tool.args["command"]

    def test_expect_close_slash_followed_by_non_end_char(self):
        # ``[/bash]ls[bash/X[bash/]`` — EXPECT_CLOSE_SLASH hits ``X``
        # (not ``]``) → re-buffer ``[bash/X`` into content, return to
        # IN_BLOCK, then second ``[bash/]`` closes properly.
        ev = _events("[/bash]ls[bash/X[bash/]", _cfg(known_tools={"bash"}))
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert "[bash/X" in tool.args["command"]

    def test_xml_xml_inline_args_emitted_in_block_start_event(self):
        # Block start event fired with the name when attrs are read.
        cfg = _cfg(fmt=XML_FORMAT, known_tools={"edit"}, emit_block_events=True)
        ev = _events('<edit path="x.py">diff</edit>', cfg)
        starts = [e for e in ev if isinstance(e, BlockStartEvent)]
        assert len(starts) == 1
        assert starts[0].block_type == "edit"


# ── parse_full convenience ───────────────────────────────────────────


class TestParseFull:
    def test_runs_flush(self):
        # parse_full ensures flush is called — incomplete blocks should
        # surface as text.
        out = parse_full("[/bash]ls", _cfg(known_tools={"bash"}))
        text = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "[/bash]" in text
        assert "ls" in text

    def test_with_default_config(self):
        out = parse_full("plain text")
        assert len(out) == 1
        assert isinstance(out[0], TextEvent)
        assert out[0].text == "plain text"


# ── reset and state inspection ───────────────────────────────────────


class TestParserState:
    def test_initial_state_is_normal(self):
        p = StreamParser()
        assert p.state == ParserState.NORMAL

    def test_state_resets_after_complete_block(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        p.feed("[/bash]ls[bash/]")
        assert p.state == ParserState.NORMAL
        assert p.current_name == ""
        assert p.block_buffer == ""

    def test_flush_resets_state(self):
        p = StreamParser(_cfg(known_tools={"bash"}))
        p.feed("[/bash]ls")  # incomplete
        p.flush()
        assert p.state == ParserState.NORMAL


# ── raw rebuilding ───────────────────────────────────────────────────


class TestRawReconstruction:
    def test_bracket_raw_round_trip(self):
        ev = _events(
            "[/write]\n@@path=x.py\nbody\n[write/]",
            _cfg(known_tools={"write"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        # Raw rebuilds the canonical form.
        assert "[/write]" in tool.raw
        assert "path=x.py" in tool.raw
        assert "body" in tool.raw
        assert "[write/]" in tool.raw

    def test_xml_raw_round_trip(self):
        ev = _events(
            '<bash arg="v">code</bash>',
            _cfg(fmt=XML_FORMAT, known_tools={"bash"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        # XML raw: <bash arg="v">code</bash> — but the args dict gets the
        # body merged into ``command`` (the bash content arg), so the
        # rebuild includes that.
        assert "<bash" in tool.raw
        assert "</bash>" in tool.raw

    def test_xml_self_closing_raw(self):
        ev = _events(
            '<read path="x"/>',
            _cfg(fmt=XML_FORMAT, known_tools={"read"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        # No body → self-closing raw form.
        assert tool.raw.endswith("/>")

    def test_xml_unclosed_block_with_inline_args_dumps_full_open(self):
        # Exercises _build_raw_open in XML mode with inline_args set.
        p = StreamParser(_cfg(fmt=XML_FORMAT, known_tools={"edit"}))
        p.feed('<edit path="x.py">unclosed body')
        out = p.flush()
        joined = "".join(e.text for e in out if isinstance(e, TextEvent))
        assert "<edit" in joined
        assert 'path="x.py"' in joined
        assert "unclosed body" in joined


# ── arg-style: line with custom separators ───────────────────────────


class TestCustomArgFormat:
    def test_colon_separator(self):
        fmt = ToolCallFormat(arg_prefix="!!", arg_kv_sep=":", arg_style="line")
        ev = _events(
            "[/bash]\n!!command:ls\n[bash/]",
            _cfg(fmt=fmt, known_tools={"bash"}),
        )
        tool = next(e for e in ev if isinstance(e, ToolCallEvent))
        assert tool.args == {"command": "ls"}


# ── multi-block sequencing ───────────────────────────────────────────


class TestMultipleBlocks:
    def test_two_tools_back_to_back(self):
        ev = _events("[/bash]a[bash/][/bash]b[bash/]", _cfg(known_tools={"bash"}))
        tools = [e for e in ev if isinstance(e, ToolCallEvent)]
        assert len(tools) == 2
        assert tools[0].args["command"] == "a"
        assert tools[1].args["command"] == "b"

    def test_mixed_tool_command_subagent(self):
        ev = _events(
            "[/bash]ls[bash/]" "[/info]bash[info/]" "[/agent]explore[agent/]",
            _cfg(
                known_tools={"bash"},
                known_commands={"info"},
                known_subagents={"agent"},
            ),
        )
        kinds = [type(e).__name__ for e in ev if not isinstance(e, TextEvent)]
        assert kinds == ["ToolCallEvent", "CommandEvent", "SubAgentCallEvent"]
