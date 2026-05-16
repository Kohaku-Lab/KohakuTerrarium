"""Integration test for the ``parsing/`` package.

This file is the comprehensive usage example of ``kohakuterrarium.parsing``.
Each test method runs one COMPLETE workflow end-to-end — never a granular
per-method probe.

The real consumer of ``StreamParser`` is ``core/controller.py``: it builds a
parser from the registry, feeds the LLM's streamed output to ``feed()``
chunk-by-chunk, drains the tail with ``flush()``, handles ``CommandEvent``
inline and yields every other ``ParseEvent``. These tests mirror that exact
lifecycle — including markers split across ``feed()`` chunk boundaries, which
is the whole reason the parser is a streaming state machine.

Two paths are exercised:
  1. Driving the real ``StreamParser`` directly with realistically chunked
     strings (bracket + XML formats).
  2. Running a real ``Agent``-equivalent turn through the real ``Controller``
     (via ``TestAgentBuilder``) with ``ScriptedLLM``, asserting the controller
     acted on a parser-extracted tool call.
"""

from typing import Any

import pytest

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.parsing import (
    CommandEvent,
    ParserConfig,
    StreamParser,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
    extract_subagent_calls,
    extract_text,
    extract_tool_calls,
)
from kohakuterrarium.parsing.events import (
    BlockEndEvent,
    BlockStartEvent,
    OutputCallEvent,
    is_action_event,
    is_text_event,
)
from kohakuterrarium.parsing.format import (
    BRACKET_FORMAT,
    XML_FORMAT,
    format_tool_call_example,
)
from kohakuterrarium.parsing.patterns import (
    build_tool_args,
    is_command_tag,
    is_output_tag,
    is_subagent_tag,
    is_tool_tag,
    parse_attributes,
    parse_closing_tag,
    parse_opening_tag,
)
from kohakuterrarium.parsing.state_machine import parse_full
from kohakuterrarium.testing.agent import TestAgentBuilder

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_at_boundaries(text: str, cut_points: list[int]) -> list[str]:
    """Split ``text`` into chunks at the given absolute offsets.

    Used to feed the parser the way a streaming provider would — with tool /
    command markers deliberately torn across chunk boundaries.
    """
    chunks: list[str] = []
    prev = 0
    for cut in cut_points:
        chunks.append(text[prev:cut])
        prev = cut
    chunks.append(text[prev:])
    return [c for c in chunks if c != ""]


def _feed_chunks(parser: StreamParser, chunks: list[str]) -> list[Any]:
    """Mirror the controller loop: feed every chunk, then flush the tail."""
    events: list[Any] = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.flush())
    return events


class _EchoTool(BaseTool):
    """A deterministic tool so tool-result assertions can be exact.

    Echoes its ``command`` argument back verbatim — that is exactly the arg
    name the parser maps a bracket body onto for a tool named ``echo`` only if
    configured; here we use the default ``content`` arg mapping by giving the
    tool the name ``echo`` (not in DEFAULT_CONTENT_ARG_MAP, so body -> content).
    """

    needs_context = False

    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the content back verbatim."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def execute(
        self, args: dict[str, Any], context: ToolContext | None = None
    ) -> ToolResult:
        return ToolResult(output=f"ECHO::{args.get('content', '')}", exit_code=0)


class TestParsingIntegration:
    """End-to-end workflows for the streaming parser."""

    def test_bracket_stream_mixed_text_tool_command(self):
        """WORKFLOW: a full bracket-format assistant turn streamed in chunks.

        The assistant emits: plain text, a ``bash`` tool call, a ``##info##``
        framework command, then closing text. Every marker is split across
        ``feed()`` boundaries. Assert the EXACT event sequence and EXACT
        payloads — tool name + args, command + args, the literal text — and
        that ``flush()`` drained the trailing text.
        """
        # Parser configured the way the controller configures it: known tools
        # come from the registry, known commands from the controller command
        # table. ``info`` maps its body to ``tool_name`` via DEFAULT_CONTENT_ARG_MAP.
        config = ParserConfig(
            known_tools={"bash", "read"},
            known_commands={"info", "wait"},
            tool_format=BRACKET_FORMAT,
        )
        parser = StreamParser(config)

        full = (
            "Let me check the directory.\n"
            "[/bash]\n"
            "@@timeout=30\n"
            "ls -la\n"
            "[bash/]\n"
            "Now I need the docs.\n"
            "[/info]\n"
            "read\n"
            "[info/]\n"
            "All done."
        )
        # Cut points chosen to tear EVERY structural marker:
        #   - inside "[/bash]"  -> "[" | "/bash]"
        #   - inside "[bash/]"  -> "[bas" | "h/]"
        #   - inside "[/info]"  -> "[/in" | "fo]"
        #   - inside "[info/]"  -> "[info" | "/]"
        cuts = [
            full.index("[/bash]") + 1,
            full.index("[bash/]") + 4,
            full.index("[/info]") + 4,
            full.index("[info/]") + 5,
        ]
        chunks = _chunk_at_boundaries(full, sorted(cuts))
        # Sanity: a marker really is torn across a boundary.
        assert any(c.endswith("[") for c in chunks)

        events = _feed_chunks(parser, chunks)

        # Exact sequence: text, tool, text, command, text.
        assert [type(e).__name__ for e in events] == [
            "TextEvent",
            "ToolCallEvent",
            "TextEvent",
            "CommandEvent",
            "TextEvent",
        ]

        text0, tool, text1, command, text2 = events

        assert isinstance(text0, TextEvent)
        assert text0.text == "Let me check the directory.\n"

        assert isinstance(tool, ToolCallEvent)
        assert tool.name == "bash"
        # ``@@timeout=30`` parsed as inline arg; body mapped to "command"
        # because DEFAULT_CONTENT_ARG_MAP maps bash -> command.
        assert tool.args == {"timeout": "30", "command": "ls -la"}

        assert isinstance(text1, TextEvent)
        assert text1.text == "\nNow I need the docs.\n"

        assert isinstance(command, CommandEvent)
        assert command.command == "info"
        # ``info`` body mapped through DEFAULT_CONTENT_ARG_MAP -> tool_name,
        # but CommandEvent carries the raw stripped body as ``args``.
        assert command.args == "read"

        assert isinstance(text2, TextEvent)
        # flush() drained the trailing un-terminated text.
        assert text2.text == "\nAll done."

        # The controller routes events by kind — is_action_event /
        # is_text_event are the predicates it uses to split the stream.
        assert [is_text_event(e) for e in events] == [
            True,
            False,
            True,
            False,
            True,
        ]
        assert [is_action_event(e) for e in events] == [
            False,
            True,
            False,
            True,
            False,
        ]

        # --- a second turn on a FRESH parser: an explicit output block +
        # block-start/block-end events. ``output_<target>`` blocks route
        # to a named output module; ``emit_block_events`` is the early-
        # allocation signal the controller can opt into.
        out_config = ParserConfig(
            known_tools={"bash"},
            known_commands={"info"},
            known_outputs={"discord"},
            tool_format=BRACKET_FORMAT,
            emit_block_events=True,
        )
        out_parser = StreamParser(out_config)
        out_full = (
            "Posting now.\n"
            "[/output_discord]\n"
            "hello channel\n"
            "[output_discord/]\n"
            "Posted."
        )
        out_events = _feed_chunks(out_parser, _chunk_at_boundaries(out_full, [20, 45]))
        # text, block-start, output-call, block-end, text — block events
        # bracket the output block.
        assert [type(e).__name__ for e in out_events] == [
            "TextEvent",
            "BlockStartEvent",
            "OutputCallEvent",
            "BlockEndEvent",
            "TextEvent",
        ]
        block_start = out_events[1]
        output_call = out_events[2]
        block_end = out_events[3]
        assert isinstance(block_start, BlockStartEvent)
        # BlockStartEvent's first positional field is named ``block_type``
        # but the parser passes the block NAME — so for an output block it
        # carries the full ``output_discord`` tag.
        assert block_start.block_type == "output_discord"
        assert isinstance(output_call, OutputCallEvent)
        assert output_call.target == "discord"
        assert output_call.content == "hello channel"
        assert isinstance(block_end, BlockEndEvent)
        assert block_end.block_type == "output_discord"

        # An ``output_`` tag whose target is NOT in known_outputs is not an
        # output block — it falls through to "unknown block" -> raw text.
        unknown_out = StreamParser(
            ParserConfig(known_outputs={"discord"}, tool_format=BRACKET_FORMAT)
        )
        uo_events = _feed_chunks(unknown_out, ["[/output_slack]\nhi\n[output_slack/]"])
        assert all(isinstance(e, TextEvent) for e in uo_events)
        assert "[/output_slack]" in "".join(e.text for e in uo_events)

        # ``parse_full`` is the non-streaming convenience the codebase uses
        # for one-shot parsing — it must agree with the streamed result.
        pf_events = parse_full(out_full, out_config)
        assert [type(e).__name__ for e in pf_events] == [
            type(e).__name__ for e in out_events
        ]

        # --- the ``parsing/__init__`` extraction helpers — the public
        # convenience API a consumer uses to sift a finished event list.
        # Re-parse the FIRST turn (text/tool/text/command/text) to get a
        # mixed list, then pull each kind out.
        mixed = parse_full(full, config)
        extracted_tools = extract_tool_calls(mixed)
        assert [t.name for t in extracted_tools] == ["bash"]
        assert extracted_tools[0].args == {"timeout": "30", "command": "ls -la"}
        # No sub-agents in this turn.
        assert extract_subagent_calls(mixed) == []
        # extract_text concatenates every TextEvent's text verbatim.
        assert extract_text(mixed) == (
            "Let me check the directory.\n\nNow I need the docs.\n\nAll done."
        )

        # --- ``format_tool_call_example`` — the prompt-generator helper
        # that renders a format-correct example for ANY ToolCallFormat.
        # Bracket: line-style ``@@key=value`` args, [/name] open + [name/]
        # close.
        bracket_example = format_tool_call_example(
            BRACKET_FORMAT, "bash", {"timeout": "30"}, body="ls -la"
        )
        assert bracket_example == "[/bash]\n@@timeout=30\nls -la\n[bash/]"
        # No args / no body -> just the open + close markers.
        assert format_tool_call_example(BRACKET_FORMAT, "info") == "[/info]\n[info/]"
        # XML: inline ``key="value"`` attributes, <name ...> open + </name>
        # close.
        xml_example = format_tool_call_example(
            XML_FORMAT, "read", {"path": "src/main.py"}, body="show me"
        )
        assert xml_example == '<read path="src/main.py">\nshow me\n</read>'
        # XML self-closing-ish form: attrs only, no body.
        assert (
            format_tool_call_example(XML_FORMAT, "write", {"path": "out.txt"})
            == '<write path="out.txt">\n</write>'
        )

        # --- the ``parsing/patterns`` public predicate + builder API.
        # ``parse_opening_tag`` on an XML opening tag -> (name, attrs,
        # self_closing).
        assert parse_opening_tag('<read path="x.py">') == (
            "read",
            {"path": "x.py"},
            False,
        )
        assert parse_opening_tag("<write/>") == ("write", {}, True)
        assert parse_opening_tag("not a tag") is None
        # ``parse_closing_tag`` extracts the bare name (or None).
        assert parse_closing_tag("</read>") == "read"
        assert parse_closing_tag("<read>") is None
        # ``parse_attributes`` pulls every key="value" pair.
        assert parse_attributes(' path="a.py" limit="50"') == {
            "path": "a.py",
            "limit": "50",
        }
        # ``build_tool_args`` maps the block body onto the tool's content
        # arg (bash -> command) but never overrides an explicit attribute.
        assert build_tool_args("bash", {}, "ls -la") == {"command": "ls -la"}
        assert build_tool_args("read", {"path": "set"}, "ignored body") == {
            "path": "set"
        }
        # A tool with no content-arg-map entry falls back to "content".
        assert build_tool_args("frob", {}, "stuff") == {"content": "stuff"}
        # ``is_*_tag`` — the registry-driven classification predicates.
        assert is_tool_tag("bash", {"bash", "read"}) is True
        assert is_tool_tag("frob", {"bash"}) is False
        assert is_tool_tag("bash", None) is False  # no registry -> nothing is a tool
        assert is_subagent_tag("agent", {"agent"}) is True
        assert is_subagent_tag("agent") is True  # default subagent set
        assert is_command_tag("info", {"info"}) is True
        assert is_command_tag("info") is True  # default command set
        # ``is_output_tag`` -> (is_output, target). The "output_" prefix is
        # stripped; an empty / unknown target is rejected.
        assert is_output_tag("output_discord", {"discord"}) == (True, "discord")
        assert is_output_tag("output_slack", {"discord"}) == (False, "")
        assert is_output_tag("output_", {"discord"}) == (False, "")
        assert is_output_tag("notoutput") == (False, "")
        # With no known_outputs set, any non-empty target is accepted.
        assert is_output_tag("output_anything") == (True, "anything")

    def test_xml_stream_tool_subagent_selfclosing(self):
        """WORKFLOW: a full XML-format assistant turn streamed in chunks.

        Covers the second ``ToolCallFormat`` variant. The assistant emits
        text, an XML tool call with an inline attribute + body, an XML
        sub-agent call, and a self-closing XML tool tag with no body. Markers
        are split across ``feed()`` boundaries. Assert the exact event
        sequence and payloads.
        """
        config = ParserConfig(
            known_tools={"read", "write"},
            known_subagents={"agent"},
            tool_format=XML_FORMAT,
        )
        parser = StreamParser(config)

        full = (
            "Reading the file now."
            '<read path="src/main.py">show me</read>'
            "Delegating."
            "<agent>summarize the repo</agent>"
            'Touching it.<write path="out.txt"/>'
        )
        # Tear markers:
        #   inside "<read ..."   -> "<rea" | "d path=..."
        #   inside "</read>"     -> "</rea" | "d>"
        #   inside "<agent>"     -> "<age" | "nt>"
        #   inside "<write .../>"-> "<writ" | 'e path="out.txt"/>'
        cuts = sorted(
            [
                full.index("<read") + 4,
                full.index("</read>") + 5,
                full.index("<agent>") + 4,
                full.index("<write") + 5,
            ]
        )
        chunks = _chunk_at_boundaries(full, cuts)

        events = _feed_chunks(parser, chunks)

        assert [type(e).__name__ for e in events] == [
            "TextEvent",
            "ToolCallEvent",
            "TextEvent",
            "SubAgentCallEvent",
            "TextEvent",
            "ToolCallEvent",
        ]

        text0, read_tool, text1, subagent, text2, write_tool = events

        assert isinstance(text0, TextEvent)
        assert text0.text == "Reading the file now."

        assert isinstance(read_tool, ToolCallEvent)
        assert read_tool.name == "read"
        # XML inline attr ``path`` set; body "show me" -> content arg. For
        # ``read`` DEFAULT_CONTENT_ARG_MAP maps content -> "path", but ``path``
        # is already set via attribute, so the body is dropped (not overridden).
        assert read_tool.args == {"path": "src/main.py"}

        assert isinstance(text1, TextEvent)
        assert text1.text == "Delegating."

        assert isinstance(subagent, SubAgentCallEvent)
        assert subagent.name == "agent"
        assert subagent.args == {"task": "summarize the repo"}

        assert isinstance(text2, TextEvent)
        assert text2.text == "Touching it."

        assert isinstance(write_tool, ToolCallEvent)
        assert write_tool.name == "write"
        # Self-closing tag: only the inline attribute, no body.
        assert write_tool.args == {"path": "out.txt"}

        # --- mismatched XML close marker: ``<read>...</write>`` is not a
        # valid close for ``read``; the parser logs a warning and folds
        # the stray ``</write>`` back into the block content rather than
        # ending the block. The block then runs to its REAL close.
        mismatch = StreamParser(
            ParserConfig(known_tools={"read"}, tool_format=XML_FORMAT)
        )
        mm_events = _feed_chunks(mismatch, ["<read>before </write> after</read>tail"])
        mm_tools = [e for e in mm_events if isinstance(e, ToolCallEvent)]
        assert len(mm_tools) == 1
        # The mismatched close is preserved verbatim inside the body.
        assert mm_tools[0].args == {"path": "before </write> after"}
        assert mm_events[-1].text == "tail"

        # --- an XML framework command + a self-closing tag flushed mid-
        # attributes. ``flush()`` must emit any half-built marker as raw
        # text so nothing is silently dropped at stream end.
        cmd_parser = StreamParser(
            ParserConfig(known_commands={"info"}, tool_format=XML_FORMAT)
        )
        cmd_events = _feed_chunks(cmd_parser, ["pre <info>read</info> post"])
        cmd = [e for e in cmd_events if isinstance(e, CommandEvent)]
        assert len(cmd) == 1
        assert cmd[0].command == "info"
        assert cmd[0].args == "read"

        # A stream that ends mid-tag: flush() recovers the partial marker
        # as text instead of losing it.
        partial = StreamParser(
            ParserConfig(known_tools={"write"}, tool_format=XML_FORMAT)
        )
        partial_events = partial.feed('done<write path="x.txt"')
        partial_events.extend(partial.flush())
        recovered = "".join(e.text for e in partial_events if isinstance(e, TextEvent))
        assert recovered.startswith("done")
        assert 'write path="x.txt"' in recovered

    def test_char_by_char_stream_matches_bulk_parse(self):
        """WORKFLOW: the worst-case streaming — one character per ``feed()``.

        ScriptedLLM streams in chunks (default 10 chars); a real provider can
        emit single tokens. Feeding char-by-char is the maximal stress on the
        state machine's partial-marker handling. The concatenation of all
        emitted events must be identical to a single bulk ``feed()`` — the
        parser must be chunk-invariant.
        """
        config = ParserConfig(
            known_tools={"bash"},
            known_commands={"info"},
            tool_format=BRACKET_FORMAT,
        )
        full = (
            "before\n"
            "[/bash]\n"
            "echo nested ] bracket and [ too\n"
            "[bash/]\n"
            "after"
        )

        char_parser = StreamParser(config)
        char_events = _feed_chunks(char_parser, list(full))

        bulk_parser = StreamParser(config)
        bulk_events = _feed_chunks(bulk_parser, [full])

        def normalize(events: list[Any]) -> list[tuple]:
            out: list[tuple] = []
            for e in events:
                if isinstance(e, TextEvent):
                    out.append(("text", e.text))
                elif isinstance(e, ToolCallEvent):
                    out.append(("tool", e.name, tuple(sorted(e.args.items()))))
                elif isinstance(e, CommandEvent):
                    out.append(("cmd", e.command, e.args))
            # Merge adjacent text events — chunking can split text emission.
            merged: list[tuple] = []
            for item in out:
                if item[0] == "text" and merged and merged[-1][0] == "text":
                    merged[-1] = ("text", merged[-1][1] + item[1])
                else:
                    merged.append(item)
            return merged

        assert normalize(char_events) == normalize(bulk_events)
        # And the tool call survived: body becomes the "command" arg, the
        # stray ``]`` and ``[`` inside the block are preserved as content.
        tool_calls = [e for e in char_events if isinstance(e, ToolCallEvent)]
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "bash"
        assert tool_calls[0].args == {"command": "echo nested ] bracket and [ too"}

        # --- every "incomplete state" flush() path, fed char-by-char so
        # the parser is genuinely left mid-marker at stream end. Each one
        # must come back as recoverable raw text, never be dropped.
        for partial_text, expected_substr in [
            ("text [", "["),  # MAYBE_OPEN
            ("text [/", "[/"),  # OPEN_SLASH
            ("text [/bas", "[/bas"),  # IN_OPEN_NAME
            ("[/bash]\nbody", "[/bash]"),  # IN_BLOCK (unclosed)
            ("[/bash]\nbody[", "body["),  # MAYBE_CLOSE
            ("[/bash]\nbody[bas", "[bas"),  # IN_CLOSE_NAME
            # EXPECT_CLOSE_SLASH: flush re-emits ``[<name>`` (the trailing
            # ``/`` it was waiting on never arrived, so it isn't echoed).
            ("[/bash]\nbody[bash/", "body[bash"),  # EXPECT_CLOSE_SLASH
        ]:
            p = StreamParser(config)
            ev = _feed_chunks(p, list(partial_text))
            text = "".join(e.text for e in ev if isinstance(e, TextEvent))
            assert expected_substr in text, partial_text

        # An unterminated bracket block: flush() warns + emits the whole
        # raw block (open marker + buffered body) as a single TextEvent.
        unclosed = StreamParser(config)
        unclosed_ev = _feed_chunks(unclosed, ["[/bash]\necho hi\nno close here"])
        unclosed_text = "".join(e.text for e in unclosed_ev if isinstance(e, TextEvent))
        assert "[/bash]" in unclosed_text
        assert "echo hi" in unclosed_text
        # Nothing was misclassified as a tool call.
        assert not any(isinstance(e, ToolCallEvent) for e in unclosed_ev)

        # --- bracket-format content edge cases that LOOK like markers
        # but aren't — every one must stay inside the block body, never
        # split it or be misread as a close. Fed char-by-char so the
        # state machine genuinely transitions through each branch.
        for body_with_traps, expected_command in [
            # [name] without a slash -> not a close tag, stays content.
            ("[/bash]\nls [bash] more\n[bash/]", "ls [bash] more"),
            # [/ inside the block -> not a nested open, stays content.
            ("[/bash]\nls [/grep] x\n[bash/]", "ls [/grep] x"),
            # A mismatched bracket close [wrong/] -> content, real close wins.
            ("[/bash]\nls [wrong/] y\n[bash/]", "ls [wrong/] y"),
            # [name<non-]> char during close-name read -> back to content.
            ("[/bash]\nls [bash!x\n[bash/]", "ls [bash!x"),
            # [/<digit> from NORMAL is not a valid bracket open name start —
            # wait, [/ then non-alnum: emit as text. Tested via flush above;
            # here ensure [/ then space inside a fresh stream is text.
        ]:
            p = StreamParser(config)
            ev = _feed_chunks(p, list(body_with_traps))
            tcs = [e for e in ev if isinstance(e, ToolCallEvent)]
            assert len(tcs) == 1, body_with_traps
            assert tcs[0].args == {"command": expected_command}, body_with_traps

        # ``[`` followed by a non-tag char (bracket needs ``[/``): the
        # ``[x`` is plain text, nothing is misparsed.
        plain_bracket = StreamParser(config)
        pb_ev = _feed_chunks(plain_bracket, list("array[0] = 1"))
        assert extract_text(pb_ev) == "array[0] = 1"
        assert not any(isinstance(e, ToolCallEvent) for e in pb_ev)
        # ``[/`` followed by a non-name char is also just text.
        slash_text = StreamParser(config)
        st_ev = _feed_chunks(slash_text, list("path[/ ]end"))
        assert "[/" in extract_text(st_ev)

        # --- XML-format content edge cases + every XML partial-flush
        # branch (the mirror of the bracket flush sweep above). XML uses
        # ``<name>`` open and ``</name>`` close.
        xml_cfg = ParserConfig(known_tools={"read"}, tool_format=XML_FORMAT)
        for xml_body, xml_expected in [
            # <letter inside block -> HTML-ish content, not a close.
            ("<read>see <b>bold</b> text</read>", "see <b>bold</b> text"),
            # </wrong> mismatched close -> content, real </read> wins.
            ("<read>a </wrong> b</read>", "a </wrong> b"),
        ]:
            p = StreamParser(xml_cfg)
            ev = _feed_chunks(p, list(xml_body))
            tcs = [e for e in ev if isinstance(e, ToolCallEvent)]
            assert len(tcs) == 1, xml_body
            assert tcs[0].args == {"path": xml_expected}, xml_body
        # XML ``</`` from NORMAL (no open block) is plain text.
        xml_norm = StreamParser(xml_cfg)
        xn_ev = _feed_chunks(xml_norm, list("done </read> tail"))
        assert "</read>" in extract_text(xn_ev)
        assert not any(isinstance(e, ToolCallEvent) for e in xn_ev)
        # Every XML partial-flush state recovers its half-built marker as
        # text — the slash_means_open=False mirror of the bracket sweep.
        for xml_partial, xml_substr in [
            ("text <", "<"),  # MAYBE_OPEN
            ("text <rea", "<rea"),  # IN_OPEN_NAME
            ('text <read path="x"', 'read path="x"'),  # IN_OPEN_ATTRS
            ('text <read path="x"/', "/"),  # IN_SELF_CLOSING
            ("<read>body", "<read>"),  # IN_BLOCK unclosed
            ("<read>body<", "body<"),  # MAYBE_CLOSE inside block
            ("<read>body</rea", "</rea"),  # IN_CLOSE_NAME
        ]:
            p = StreamParser(xml_cfg)
            ev = _feed_chunks(p, list(xml_partial))
            assert xml_substr in extract_text(ev), xml_partial

    def test_unknown_block_falls_back_to_text(self):
        """WORKFLOW: a block whose name is not a known tool/command/subagent.

        The controller builds the parser's ``known_tools`` from the live
        registry. If the LLM hallucinates ``[/frobnicate]...`` and no such
        tool is registered, the parser must NOT emit a ToolCallEvent — it
        emits the raw block as a TextEvent so nothing is silently swallowed.
        Real ``[/bash]`` text around it must still parse.
        """
        config = ParserConfig(
            known_tools={"bash"},  # frobnicate is NOT here
            known_commands={"info"},
            tool_format=BRACKET_FORMAT,
        )
        parser = StreamParser(config)

        full = (
            "start "
            "[/frobnicate]\ndo magic\n[frobnicate/]"
            " mid "
            "[/bash]\nls\n[bash/]"
            " end"
        )
        chunks = _chunk_at_boundaries(
            full,
            sorted(
                [
                    full.index("[/frobnicate]") + 2,
                    full.index("[frobnicate/]") + 6,
                    full.index("[/bash]") + 3,
                ]
            ),
        )
        events = _feed_chunks(parser, chunks)

        tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tool_calls) == 1
        assert tool_calls[0].name == "bash"
        assert tool_calls[0].args == {"command": "ls"}

        # The unknown block became text — its raw form is recoverable.
        all_text = "".join(e.text for e in events if isinstance(e, TextEvent))
        assert "[/frobnicate]" in all_text
        assert "do magic" in all_text
        assert "[frobnicate/]" in all_text
        assert all_text.startswith("start ")
        assert all_text.endswith(" end")

        # --- a known sub-agent vs an unknown one: a real ``[/agent]``
        # block (``agent`` is in the default subagent set) becomes a
        # SubAgentCallEvent; an unregistered subagent name does not.
        sa_parser = StreamParser(
            ParserConfig(
                known_tools=set(),
                known_subagents={"agent"},
                tool_format=BRACKET_FORMAT,
            )
        )
        sa_events = _feed_chunks(
            sa_parser,
            ["[/agent]\nsummarize the repo\n[agent/] then [/ghost]\nx\n[ghost/]"],
        )
        subagents = [e for e in sa_events if isinstance(e, SubAgentCallEvent)]
        assert len(subagents) == 1
        assert subagents[0].name == "agent"
        assert subagents[0].args == {"task": "summarize the repo"}
        # The unknown ``ghost`` subagent fell back to raw text.
        assert "[/ghost]" in "".join(
            e.text for e in sa_events if isinstance(e, TextEvent)
        )

        # The event dataclasses' __repr__ / __bool__ are what logging and
        # truthiness checks in the controller rely on.
        assert repr(subagents[0]).startswith("SubAgentCallEvent(name='agent'")
        assert repr(tool_calls[0]).startswith("ToolCallEvent(name='bash'")
        assert bool(TextEvent("x")) is True
        assert bool(TextEvent("")) is False
        cmd_ev = CommandEvent(command="info", args="bash")
        assert repr(cmd_ev) == "CommandEvent(command='info', args='bash')"
        out_ev = OutputCallEvent(target="discord", content="hi there")
        assert "discord" in repr(out_ev)

    async def test_controller_acts_on_parser_extracted_tool_call(self):
        """WORKFLOW: real Controller turn — parser output drives a tool run.

        This is the production path. ``TestAgentBuilder`` wires a real
        ``Controller`` (which builds a real ``StreamParser`` from the registry)
        + a real ``Executor``. ``ScriptedLLM`` streams a bracket-format tool
        call in 10-char chunks — markers naturally land across chunk
        boundaries. The controller feeds the parser, yields a ToolCallEvent,
        and ``TestAgentEnv.inject`` submits it to the executor.

        Assert: the parser extracted the exact tool name + args from the
        stream, and the real tool actually ran with those args (its
        deterministic output appears in the job result).
        """
        builder = TestAgentBuilder()
        builder.with_system_prompt("You are a test agent.")
        builder.with_tool(_EchoTool())
        # ScriptedLLM default chunk_size=10: "[/echo]\n..." is torn across
        # feed() boundaries exactly like a real streaming provider.
        builder.with_llm_script(
            ["Working on it.\n" "[/echo]\n" "hello parser\n" "[echo/]\n" "Submitted."]
        )
        env = builder.build()

        await env.inject("please echo something")

        # The controller's parser saw the LLM stream and recognised ``echo``
        # as a known tool (it is in the registry), so a tool_start activity
        # fired — proving a ToolCallEvent was yielded, not raw text.
        tool_starts = env.output.activities_of_type("tool_start")
        assert len(tool_starts) == 1
        assert tool_starts[0].detail.startswith("[echo] ")

        # Surrounding text streamed through untouched.
        assert "Working on it." in env.output.all_text
        assert "Submitted." in env.output.all_text
        # The tool block itself must NOT appear as user-facing text.
        assert "[/echo]" not in env.output.all_text
        assert "[echo/]" not in env.output.all_text

        # ``submit_from_event`` runs the tool as a background asyncio task —
        # mirror the agent's processing loop, which awaits in-flight tool jobs
        # before reading their results.
        job_id = tool_starts[0].detail.split(" ", 1)[1]
        results = await env.executor.wait_all(timeout=10)
        assert job_id in results

        # The real tool ran with the EXACT args the parser extracted from the
        # stream: body "hello parser" -> "content" arg (echo is not in
        # DEFAULT_CONTENT_ARG_MAP, so default content arg is used).
        result = results[job_id]
        assert result.output == "ECHO::hello parser"
        assert result.error is None

    async def test_controller_handles_command_inline_during_stream(self):
        """WORKFLOW: real Controller turn — a ``##info##`` command in stream.

        The controller treats ``CommandEvent`` specially: it executes the
        command inline during ``_run_text_completion`` and yields a
        ``CommandResultEvent`` instead. ``TestAgentEnv.inject`` turns that into
        a ``command_done`` / ``command_error`` activity. This exercises the
        parser -> controller -> command-handler path with the marker streamed
        in chunks.

        Assert: the parser extracted the ``info`` command, the controller ran
        it inline, and the surrounding text still streamed.
        """
        builder = TestAgentBuilder()
        builder.with_system_prompt("You are a test agent.")
        builder.with_builtin_tools(["bash"])
        # ``info`` is a real controller command; ask it about ``bash`` which
        # is a registered builtin tool, so the command succeeds.
        builder.with_llm_script(["Checking docs.\n[/info]\nbash\n[info/]\nGot them."])
        env = builder.build()

        await env.inject("what does bash do")

        # The command was extracted by the parser and run inline -> a
        # command_done activity (not command_error, not tool_start).
        done = env.output.activities_of_type("command_done")
        assert len(done) == 1
        assert done[0].detail == "[info] OK"
        assert env.output.activities_of_type("command_error") == []
        assert env.output.activities_of_type("tool_start") == []

        # Surrounding narration streamed; the command markers did not leak as
        # user-facing text.
        assert "Checking docs." in env.output.all_text
        assert "Got them." in env.output.all_text
        assert "[/info]" not in env.output.all_text
        assert "[info/]" not in env.output.all_text
