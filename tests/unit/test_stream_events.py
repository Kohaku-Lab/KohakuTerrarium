"""Tests for StreamOutput event parsing and metadata forwarding."""

from kohakuterrarium.studio.attach._event_stream import _parse_detail


class TestParseDetail:
    """Verify _parse_detail handles nested brackets correctly."""

    def test_simple_bracket(self):
        name, detail = _parse_detail("[bash] OK")
        assert name == "bash"
        assert detail == "OK"

    def test_bracket_with_short_id(self):
        name, detail = _parse_detail("[bash[abc123]] OK")
        assert name == "bash[abc123]"
        assert detail == "OK"

    def test_subagent_label(self):
        name, detail = _parse_detail("[agent_researcher[abc123]] some task")
        assert name == "agent_researcher[abc123]"
        assert detail == "some task"

    def test_subagent_done_label(self):
        name, detail = _parse_detail("[researcher[abc123]] tools: bash, read")
        assert name == "researcher[abc123]"
        assert detail == "tools: bash, read"

    def test_no_brackets(self):
        name, detail = _parse_detail("plain text")
        assert name == "unknown"
        assert detail == "plain text"

    def test_bracket_no_trailing(self):
        name, detail = _parse_detail("[name]")
        assert name == "name"
        assert detail == ""

    def test_empty_string(self):
        name, detail = _parse_detail("")
        assert name == "unknown"
        assert detail == ""

    def test_bracket_with_bg_tag(self):
        name, detail = _parse_detail("[bash[abc123]](bg) cmd=ls")
        # No space after first ], so falls through to endswith check
        # which also doesn't match since it doesn't end with ]
        assert name == "unknown"
        assert detail == "[bash[abc123]](bg) cmd=ls"

    def test_make_job_label_consistency(self):
        """Verify that _make_job_label produces labels that _parse_detail handles."""
        from kohakuterrarium.core.agent_handlers import _make_job_label

        # Tool job
        tool_name, label = _make_job_label("bash_abc123")
        detail = f"[{label}] OK"
        parsed_name, parsed_detail = _parse_detail(detail)
        assert parsed_name == label
        assert parsed_detail == "OK"

        # Sub-agent job
        _, sa_label = _make_job_label("agent_researcher_xyz789")
        detail = f"[{sa_label}] some task"
        parsed_name, parsed_detail = _parse_detail(detail)
        assert parsed_name == sa_label
        assert parsed_detail == "some task"
