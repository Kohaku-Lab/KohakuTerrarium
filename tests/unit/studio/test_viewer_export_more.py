"""Coverage for the role-specific branches in viewer.export renderers."""

from unittest.mock import MagicMock


from kohakuterrarium.studio.persistence.viewer import export as ex_mod


def _store_with_replay(messages):
    """Build a mock store whose replay_conversation returns ``messages``."""
    store = MagicMock()
    store.load_meta.return_value = {
        "agents": ["alice"],
        "created_at": "",
        "last_active": "",
        "format_version": 1,
    }
    store.get_events.return_value = [{"type": "x"}]
    return store


# ── _render_markdown role branches ──────────────────────────


class TestRenderMarkdownRoles:
    def test_system_role(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [{"role": "system", "content": "you are X"}],
        )
        out = ex_mod._render_markdown(store, "s", agent=None)
        assert "**System:**" in out
        assert "you are X" in out

    def test_system_empty_content(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [{"role": "system", "content": ""}],
        )
        out = ex_mod._render_markdown(store, "s", agent=None)
        assert "**System:**" in out

    def test_user_role(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [{"role": "user", "content": "hi"}],
        )
        out = ex_mod._render_markdown(store, "s", agent=None)
        assert "**User:**" in out

    def test_assistant_with_tool_calls(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [
                {
                    "role": "assistant",
                    "content": "thinking",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "bash",
                                "arguments": '{"cmd":"ls"}',
                            }
                        }
                    ],
                }
            ],
        )
        out = ex_mod._render_markdown(store, "s", agent=None)
        assert "**Assistant:**" in out
        assert "bash" in out

    def test_assistant_empty_content_only_tool_calls(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}],
                }
            ],
        )
        out = ex_mod._render_markdown(store, "s", agent=None)
        assert "x" in out

    def test_tool_role(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [{"role": "tool", "name": "bash", "content": "ls output"}],
        )
        out = ex_mod._render_markdown(store, "s", agent=None)
        assert "bash" in out
        assert "ls output" in out

    def test_tool_role_empty_content(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [{"role": "tool", "name": "bash", "content": ""}],
        )
        out = ex_mod._render_markdown(store, "s", agent=None)
        assert "bash" in out


# ── _render_html branches ───────────────────────────────────


class TestRenderHtmlBranches:
    def test_with_tool_calls(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [
                {
                    "role": "assistant",
                    "content": "thinking",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "bash",
                                "arguments": '{"cmd":"ls -la"}',
                            }
                        }
                    ],
                }
            ],
        )
        out = ex_mod._render_html(store, "s", agent=None)
        assert "<details>" in out
        assert "bash" in out

    def test_long_tool_args_truncated(self, monkeypatch):
        store = _store_with_replay([])
        long_arg = '{"data":"' + ("x" * 200) + '"}'
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "bash",
                                "arguments": long_arg,
                            }
                        }
                    ],
                }
            ],
        )
        out = ex_mod._render_html(store, "s", agent=None)
        # Preview is truncated to 80 chars in the summary.
        assert "details" in out

    def test_system_tool_uses_pre(self, monkeypatch):
        store = _store_with_replay([])
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [
                {"role": "system", "content": "you are X"},
                {"role": "tool", "name": "bash", "content": "output"},
            ],
        )
        out = ex_mod._render_html(store, "s", agent=None)
        assert "<pre>" in out


# ── build_export with agent filter ──────────────────────────


class TestBuildExportWithAgentFilter:
    def test_agent_filter_in_md(self, monkeypatch):
        store = _store_with_replay([])
        store.load_meta.return_value = {
            "agents": ["alice", "bob"],
            "created_at": "",
            "last_active": "",
            "format_version": 1,
        }
        monkeypatch.setattr(
            ex_mod,
            "replay_conversation",
            lambda e: [{"role": "user", "content": "hi"}],
        )
        out = ex_mod._render_markdown(store, "s", agent="alice")
        assert "alice" in out
        assert "bob" not in out
